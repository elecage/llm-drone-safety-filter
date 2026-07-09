"""paper §C trial 격자 실행 runner — 오케스트레이션 코어 + 실행 셸.

⚠️ **스코프(ADR-0030 D5)**: `run_all`/`eval-runner` 는 *in-container 스모크/단일 trial* 용.
**본실험 데이터런(격자)은 host-driven `scripts/run_grid.py`, 풀런은 `scripts/run_full_experiment.sh`**
로 — 데이터런에 `eval-runner` 를 직접 쓰지 말 것 (sim 리셋·경계 분리는 host 가 소유).
스크립트 정본 인덱스 = [scripts/README.md](../../../scripts/README.md).

[ADR-0025 D3/D5](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md)
격자(scenario × baseline × fault × episode) → 각 trial 별 ROS 2 launch 실행 +
rosbag2 record + trial_meta.yaml 작성.

## 2계층 구조 (launch_composition / launch_description 패턴 정합)

| 계층 | 함수 | 의존성 | 검증 |
|---|---|---|---|
| **오케스트레이션 (순수)** | `RunConfig` · `select_trials` · `load_grid_json` · `trial_bag_dir` · `trial_completion_status` · `is_trial_complete` · `plan_run` · `format_plan` · `main`(--dry-run / --scan-bags / --rejudge-bags) | host venv | ✅ pytest |
| **실행 셸 (ROS 2)** | `run_trial` · `run_all` | `launch` · sim 스택 | ⚠️ Mac mini |

## ⚠️ 실행 선행 의존성 (runner 밖 — 미충족 시 run_trial 차단)

`compose_trial_node_specs` 가 참조하는 실행 파일/환경 중 *아직 미충족*:
  1. sim 라이프사이클 (gz 헤드리스 자동 시작 + trial 간 리셋) — P4, Mac mini/맥북 실측.

해소됨 (참고): `intent_llm/wrapper_node` · `intent_context/context_graph_publisher`
(세션 28) · `eval_runner/bag_reader` (#6c rosbag2_py → BagInputs, P3 — bag → 메트릭
마지막 연결).

따라서 본 모듈의 *오케스트레이션 코어* (격자 선택·계획·resume·dry-run·meta)
는 지금 완전 동작하지만, `run_trial` 의 실 launch 는 위 의존성 충족 후 Mac mini
에서 검증된다. `run_trial` 은 lazy import 로 host venv 영향 없음.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import yaml

from eval_baselines.schemas import BaselineMode

from eval_runner.bag_integrity import (
    BAG_STATUS_COMPLETE,
    BAG_STATUS_FAULT_NOT_APPLICABLE,
    BAG_STATUS_INCOMPLETE,
    check_bag_integrity,
    format_bag_status_scan,
    rejudge_trial_bag_statuses,
    scan_trial_bag_statuses,
)
from eval_runner.grid import (
    default_fault_scenario_paths,
    generate_trial_grid,
    resolve_fault_scenario_paths,
)
from eval_runner.launch_composition import DEFAULT_BACKBONE
from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec
from eval_runner.trial_meta import trial_meta_yaml_path, write_trial_meta_yaml

try:
    from eval_faults.fault_scenario import load_fault_scenario
except ImportError:  # pragma: no cover - conftest 측 경로 주입 정합
    load_fault_scenario = None  # type: ignore


DEFAULT_OUTPUT_ROOT = 'results/trials'
DEFAULT_N_EPISODES = 10
DEFAULT_EPISODE_TIMEOUT_S = 60.0
_ALL_BASELINES = tuple(m.value for m in BaselineMode)


# ----------------------------------------------------------------- config


@dataclass(frozen=True)
class RunConfig:
    """실행 구성 — 격자 선택 + 출력/실행 정책.

    Attributes
    ----------
    scenarios, baselines, faults : Sequence[str]
        격자 차원 값 — scenario_id / baseline mode value / fault scenario name.
    n_episodes : int
        cell 당 반복 수.
    output_root : Path
        trial 별 bag 디렉토리 부모 (``<output_root>/<trial_id>/``).
    resume : bool
        True 측 trial_meta.yaml 존재하는 trial 건너뜀 (idempotent 재실행).
    dry_run : bool
        True 측 실행 없이 계획만 출력.
    limit : Optional[int]
        앞에서부터 N trial 만 (smoke / 부분 실행).
    episode_timeout_s : float
        단일 trial 측 최대 실행 시간 [s] — run_trial 측 launch shutdown timer.
    backbone : str
        intent_llm wrapper 측 registry 식별자 (run-level — 격자 차원 아님).
        backbone ablation 은 backbone 별 run 반복 (ADR-0014 D5 / ADR-0025 D3
        5-dim 격자 정합). 출력은 ``<output_root>/<backbone>/<trial_id>/``.
    """

    scenarios: Sequence[str]
    baselines: Sequence[str]
    faults: Sequence[str]
    n_episodes: int
    output_root: Path
    resume: bool = False
    dry_run: bool = False
    limit: Optional[int] = None
    episode_timeout_s: float = DEFAULT_EPISODE_TIMEOUT_S
    backbone: str = DEFAULT_BACKBONE


@dataclass(frozen=True)
class TrialPlanItem:
    """단일 trial 측 실행 계획 항목 (dry-run / resume 표시).

    status:
      - 'pending'    — 미실행 (trial_meta.yaml 부재 또는 resume=False).
      - 'done'       — 완료 (resume=True + bag_status 'complete' *또는*
        'fault_not_applicable' — 후자는 명료화 후퇴로 주입 미정의, 결정론적
        거동이라 재실행해도 동일 (ADR-0037 amend) → 재실행 금지).
      - 'incomplete' — trial 시작됐으나 bag 무결성 미달 (bag_status='incomplete'
        또는 meta 손상) — run_all 측 재실행 대상.
    """

    trial: TrialSpec = field(repr=False)
    trial_id: str
    bag_dir: Path
    status: str  # 'pending' | 'done' | 'incomplete'


# ----------------------------------------------------------------- 순수 코어


def _default_fault_names() -> List[str]:
    if load_fault_scenario is None:
        raise RuntimeError('eval_faults import 불가 — PYTHONPATH 측 eval/faults 확인')
    return [load_fault_scenario(p).name for p in default_fault_scenario_paths()]


def select_trials(config: RunConfig) -> List[TrialSpec]:
    """RunConfig → list[TrialSpec] (격자 생성 + limit).

    Raises:
        ValueError: scenario/baseline/fault 무효 또는 n_episodes ≤ 0
            (generate_trial_grid 측 propagate). limit < 0.
    """
    modes = [BaselineMode(b) for b in config.baselines]  # 무효 측 ValueError
    fault_paths = resolve_fault_scenario_paths(config.faults)
    grid = generate_trial_grid(
        config.scenarios, modes, fault_paths, config.n_episodes,
    )
    if config.limit is not None:
        if config.limit < 0:
            raise ValueError(f'limit={config.limit} 무효 — 0 이상 필수')
        grid = grid[:config.limit]
    return grid


def load_grid_json(path) -> dict:
    """패널 export JSON (experiment_panel.py) 측 meta 블록 로드.

    meta = {scenarios, baselines, faults, n_episodes} — 격자 재생성용
    (개별 trial record 측 deterministic 재생성 정합, 재구성 불요).

    Returns:
        dict — scenarios / baselines / faults / n_episodes.

    Raises:
        KeyError: meta 블록 또는 필수 키 누락.
    """
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    meta = payload['meta']
    return {
        'scenarios': meta['scenarios'],
        'baselines': meta['baselines'],
        'faults': meta['faults'],
        'n_episodes': meta['n_episodes'],
    }


def trial_bag_dir(output_root, trial: TrialSpec, backbone: str) -> Path:
    """trial 측 bag 디렉토리 — ``<output_root>/<backbone>/<trial_id>/``.

    backbone 별 하위 디렉토리 — backbone sweep 시 trial_id 충돌 회피 + post-hoc
    분석 측 backbone 식별 (run-level backbone 정합).
    """
    return Path(output_root) / backbone / trial.trial_id


def trial_completion_status(bag_dir) -> str:
    """trial 완료 상태 판정 — 'missing' | 'complete' | 'incomplete' | 'fault_not_applicable'.

    세션 34 리뷰 P2 후속 — 종전 *trial_meta.yaml 존재* 만 확인하던 판정을
    bag_status 인지로 격상 (bag 기록 중 실패 trial 의 조용한 제외(silent drop)
    방지, `eval_runner.bag_integrity` 모듈 docstring).

      - trial_meta.yaml 부재 → 'missing' (미실행).
      - YAML parse 실패 / dict 아님 → 'incomplete' (재실행이 안전한 쪽).
      - bag_status='incomplete' → 'incomplete' (재실행 대상).
      - bag_status='fault_not_applicable' → 'fault_not_applicable' (제3 범주,
        ADR-0037 amend — resume 측 'done' 취급: 명료화 후퇴는 결정론적 거동이라
        재실행해도 동일, 재실행 금지).
      - bag_status='complete' *또는 키 부재(legacy meta, 본 필드 도입 전 기록)*
        → 'complete' — legacy 측 종전 resume 거동 보존. legacy 무결성 미보장은
        `scan_trial_bag_statuses` 측 'unknown' 으로 별도 보고.
    """
    path = trial_meta_yaml_path(bag_dir)
    if not path.is_file():
        return 'missing'
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        return 'incomplete'
    if not isinstance(raw, dict):
        return 'incomplete'
    status = raw.get('bag_status', BAG_STATUS_COMPLETE)
    if status == BAG_STATUS_FAULT_NOT_APPLICABLE:
        return 'fault_not_applicable'
    return 'complete' if status == BAG_STATUS_COMPLETE else 'incomplete'


def is_trial_complete(bag_dir) -> bool:
    """trial 완료 판정 (resume marker) — trial_meta.yaml 존재 + bag_status 인지.

    `trial_completion_status(bag_dir)` 가 재실행 불필요('complete' 또는
    'fault_not_applicable')인지의 bool 축약 — 기존 호출부 호환 유지.
    """
    return trial_completion_status(bag_dir) in ('complete', 'fault_not_applicable')


def plan_run(config: RunConfig) -> List[TrialPlanItem]:
    """RunConfig → 실행 계획 (각 trial 측 bag_dir + status).

    resume=True 측 trial_completion_status 측 3 분류 — 'incomplete' trial 은
    'done' 아닌 'incomplete' 로 표시되어 run_all 측 재실행된다.
    """
    items: List[TrialPlanItem] = []
    for trial in select_trials(config):
        bag_dir = trial_bag_dir(config.output_root, trial, config.backbone)
        if config.resume:
            completion = trial_completion_status(bag_dir)
            if completion in ('complete', 'fault_not_applicable'):
                # fault_not_applicable = 명료화 후퇴로 주입 미정의 (ADR-0037
                # amend) — 결정론적 거동이라 재실행해도 동일 → 'done' 취급
                # (재실행 금지). 집계 측 별도 카운트로 명시 보고.
                status = 'done'
            elif completion == 'incomplete':
                status = 'incomplete'
            else:
                status = 'pending'
        else:
            status = 'pending'
        items.append(TrialPlanItem(
            trial=trial,
            trial_id=trial.trial_id,
            bag_dir=bag_dir,
            status=status,
        ))
    return items


def format_plan(plan: Sequence[TrialPlanItem], preview_n: int = 10) -> str:
    """실행 계획 측 사람용 요약 문자열 (dry-run 출력)."""
    total = len(plan)
    done = sum(1 for it in plan if it.status == 'done')
    incomplete = sum(1 for it in plan if it.status == 'incomplete')
    pending = total - done - incomplete
    lines = [
        f'총 {total} trial — pending {pending} · done {done} '
        f'· incomplete(재실행) {incomplete}',
    ]
    for it in plan[:preview_n]:
        lines.append(f'  [{it.status:7}] {it.trial_id}  → {it.bag_dir}')
    if total > preview_n:
        lines.append(f'  ... (+{total - preview_n} trial 생략)')
    return '\n'.join(lines)


def plan_to_json_obj(plan: Sequence[TrialPlanItem]) -> dict:
    """실행 계획 → host-driven 오케스트레이션(ADR-0030 D6)용 JSON 직렬화 객체.

    host `scripts/run_grid.py` 가 본 출력을 stdlib `json` 으로 파싱해 trial 을 순회
    한다. 각 항목은 `eval-runner-one` 이 동일 TrialSpec 을 재구성할 *좌표*(scenario·
    baseline·fault name·episode) + resume 판정 status 를 담는다. bag_dir 은 host 가
    리셋·로그 진단에 참조(문자열).

    Returns:
        {'trials': [{trial_id, status, scenario, baseline, fault, episode, bag_dir}, ...]}
    """
    trials = []
    for it in plan:
        trial = it.trial
        trials.append({
            'trial_id': it.trial_id,
            'status': it.status,
            'scenario': trial.scenario_id,
            'baseline': trial.baseline_config.mode.value,
            'fault': trial.fault_scenario.name,
            'episode': trial.episode_id,
            'bag_dir': str(it.bag_dir),
        })
    return {'trials': trials}


# ----------------------------------------------------------------- 실행 셸 (ROS 2)


def run_trial(
    trial: TrialSpec,
    bag_dir,
    episode_timeout_s: float = DEFAULT_EPISODE_TIMEOUT_S,
    backbone: str = DEFAULT_BACKBONE,
) -> float:
    """단일 trial 실행 — ROS 2 launch + rosbag2 record + trial_meta.yaml.

    ⚠️ 선행 의존성 (모듈 docstring) 미충족 시 launch 측 실패. host venv 측
    `launch` import 불가 → ImportError. Mac mini Docker 측 sim 스택 기동 후 실행.

    절차:
      1. bag_dir 생성.
      2. build_launch_description(trial) + episode_timeout_s shutdown timer.
      3. LaunchService 측 trial 실행 (blocking, timeout 후 shutdown).
      4. wall_clock 측정 + `check_bag_integrity` 판정 → write_trial_meta_yaml(
         trial, wall, <bag_dir>/trial_meta.yaml, bag_status).
         incomplete 측 stdout 경고 (사유 포함) — resume 측 재실행 대상.

    Returns:
        wall_clock_s — 측정 episode 길이 [s].

    Raises:
        ImportError: host venv 측 ROS 2 unavailable.
    """
    import shutil  # noqa: WPS433

    from launch import LaunchDescription, LaunchService  # noqa: WPS433
    from launch.actions import EmitEvent, TimerAction  # noqa: WPS433
    from launch.events import Shutdown  # noqa: WPS433

    from eval_runner.launch_description import build_trial_launch_actions

    bag_path = Path(bag_dir)
    # rosbag2 record -o <dir> 는 출력 디렉토리가 *존재하면* 실패한다. bag_path 를
    # 미리 만들지 않고 부모만 생성 → rosbag 이 bag_path 를 만든다. 재실행(resume
    # incomplete)으로 기존 bag_path 가 남아 있으면 제거(incomplete 폐기). trial_meta
    # 는 run 후 bag_path 안에 기록되므로 bag(rosbag 생성)과 같은 디렉토리에 공존.
    if bag_path.exists():
        shutil.rmtree(bag_path)
    bag_path.parent.mkdir(parents=True, exist_ok=True)

    # rosbag2 출력을 bag_path 절대 경로로 — 미지정 시 CWD/trial_id 에 기록되어
    # check_bag_integrity(bag_path) 와 불일치(ADR-0030 D6 실측 발견).
    actions = build_trial_launch_actions(trial, backbone, bag_output=str(bag_path))
    actions.append(TimerAction(
        period=float(episode_timeout_s),
        actions=[EmitEvent(event=Shutdown(reason='episode timeout'))],
    ))

    service = LaunchService()
    service.include_launch_description(LaunchDescription(actions))

    t0 = time.monotonic()
    service.run()
    wall_clock_s = time.monotonic() - t0

    integrity = check_bag_integrity(
        bag_path, trial.baseline_config.mode, trial.fault_scenario.channel,
    )
    # trial 이 rosbag 생성 전 죽으면 bag_path 부재 → meta 기록 위해 보장(integrity 는
    # 이미 incomplete 판정). 정상 경로에선 rosbag 이 이미 생성.
    bag_path.mkdir(parents=True, exist_ok=True)
    write_trial_meta_yaml(
        trial, wall_clock_s, trial_meta_yaml_path(bag_path),
        bag_status=integrity.status,
    )
    if integrity.status == BAG_STATUS_INCOMPLETE:
        print(
            f'[warn] {trial.trial_id} bag incomplete — '
            + '; '.join(integrity.reasons)
        )
    elif integrity.status == BAG_STATUS_FAULT_NOT_APPLICABLE:
        # 제3 범주 — 하니스 결함 아닌 명료화 후퇴(ADR-0037 amend). 재실행 대상
        # 아님을 명시 (경고 아닌 정보).
        print(
            f'[info] {trial.trial_id} bag fault_not_applicable — '
            + '; '.join(integrity.reasons)
        )
    return wall_clock_s


def run_all(config: RunConfig) -> List[str]:
    """격자 전체 순차 실행 — resume 측 완료 trial 건너뜀 + incomplete 재실행.

    ⚠️ run_trial 측 선행 의존성 동일 (ROS 2 + sim). 반환 = 실행한 trial_id list.
    """
    executed: List[str] = []
    for item in plan_run(config):
        if item.status == 'done':
            print(f'[skip] {item.trial_id} (trial_meta 존재 + bag complete)')
            continue
        if item.status == 'incomplete':
            print(f'[rerun] {item.trial_id} (bag incomplete — 재실행)')
        else:
            print(f'[run]  {item.trial_id}')
        run_trial(item.trial, item.bag_dir, config.episode_timeout_s, config.backbone)
        executed.append(item.trial_id)
    return executed


# ----------------------------------------------------------------- CLI


def _build_config(args: argparse.Namespace) -> RunConfig:
    if args.grid_json:
        sel = load_grid_json(args.grid_json)
        scenarios = sel['scenarios']
        baselines = sel['baselines']
        faults = sel['faults']
        n_episodes = sel['n_episodes']
    else:
        scenarios = args.scenarios
        baselines = args.baselines
        faults = args.faults if args.faults is not None else _default_fault_names()
        n_episodes = args.n_episodes
    return RunConfig(
        scenarios=scenarios,
        baselines=baselines,
        faults=faults,
        n_episodes=n_episodes,
        output_root=Path(args.output_root),
        resume=args.resume,
        dry_run=args.dry_run,
        limit=args.limit,
        episode_timeout_s=args.episode_timeout_s,
        backbone=args.backbone,
    )


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description='paper §C trial 격자 실행 runner (ADR-0025 D3).',
    )
    ap.add_argument('--scenarios', nargs='+', default=list(VALID_SCENARIO_IDS),
                    help=f'scenario_id (default 전체 {list(VALID_SCENARIO_IDS)})')
    ap.add_argument('--baselines', nargs='+', default=list(_ALL_BASELINES),
                    help=f'baseline mode (default 전체 {list(_ALL_BASELINES)})')
    ap.add_argument('--faults', nargs='+', default=None,
                    help='fault scenario name (default 전체 5종)')
    ap.add_argument('--n-episodes', type=int, default=DEFAULT_N_EPISODES,
                    dest='n_episodes')
    ap.add_argument('--grid-json', default=None,
                    help='experiment_panel.py export JSON (meta 측 격자 로드)')
    ap.add_argument('--output-root', default=DEFAULT_OUTPUT_ROOT, dest='output_root')
    ap.add_argument('--resume', action='store_true', help='완료 trial 건너뜀')
    ap.add_argument('--dry-run', action='store_true', dest='dry_run',
                    help='실행 없이 계획만 출력')
    ap.add_argument('--limit', type=int, default=None, help='앞에서부터 N trial 만')
    ap.add_argument('--episode-timeout-s', type=float,
                    default=DEFAULT_EPISODE_TIMEOUT_S, dest='episode_timeout_s')
    ap.add_argument('--backbone', default=DEFAULT_BACKBONE,
                    help=f'intent_llm registry 식별자 (run-level, default {DEFAULT_BACKBONE})')
    ap.add_argument('--scan-bags', action='store_true', dest='scan_bags',
                    help='실행 없이 <output-root>/<backbone> 측 bag_status 집계만 '
                         '보고 (메트릭 집계 진입 전 게이트). incomplete 존재 시 '
                         'exit 1 (fault_not_applicable 은 실패 아님 — ADR-0037 '
                         'amend).')
    ap.add_argument('--rejudge-bags', action='store_true', dest='rejudge_bags',
                    help='--scan-bags 전에 기존 incomplete trial 을 bag+JSONL 로 '
                         '재판정해 trial_meta.yaml 갱신 (fault_not_applicable '
                         '재분류 경로, ADR-0037 amend). 단독 지정 시에도 scan '
                         '보고·게이트 exit 코드 동일 적용.')
    ap.add_argument('--plan-json', action='store_true', dest='plan_json',
                    help='실행 없이 계획을 JSON 으로 stdout 출력 (host-driven '
                         '오케스트레이션 run_grid.py 입력, ADR-0030 D6). resume 반영.')
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    if args.scan_bags or args.rejudge_bags:
        if args.rejudge_bags:
            changes = rejudge_trial_bag_statuses(
                Path(args.output_root), args.backbone,
            )
            for trial_id, old, new in changes:
                print(f'[rejudge] {trial_id}: {old} → {new}')
            print(f'[rejudge] 재분류 {len(changes)} trial')
        scan = scan_trial_bag_statuses(Path(args.output_root), args.backbone)
        print(format_bag_status_scan(scan))
        # 게이트: incomplete 만 실패 — fault_not_applicable 은 주입 미정의
        # (재실행 대상 아님, ADR-0037 amend)이라 실패로 치지 않음.
        return 1 if scan.incomplete_ids else 0

    config = _build_config(args)
    plan = plan_run(config)

    if args.plan_json:
        print(json.dumps(plan_to_json_obj(plan), ensure_ascii=False))
        return 0

    if config.dry_run:
        print(format_plan(plan))
        return 0

    print(format_plan(plan))
    executed = run_all(config)
    print(f'\n[done] 실행 {len(executed)} trial')
    # run 종료 후 bag_status 집계 명시 보고 — incomplete trial 조용한 제외 금지.
    scan = scan_trial_bag_statuses(config.output_root, config.backbone)
    print(format_bag_status_scan(scan))
    return 0


if __name__ == '__main__':
    sys.exit(main())
