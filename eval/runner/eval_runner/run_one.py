"""단일 trial 실행 CLI — host-driven 오케스트레이션(ADR-0030 D5/D6)의 컨테이너 진입.

host `scripts/run_grid.py` 가 trial *좌표*(scenario·baseline·fault name·episode)를
넘기면, 본 모듈이 동일 TrialSpec 을 deterministic 재구성하여(seed 5차원 hash, 격자
순서 *독립* — `grid.build_trial_spec`) `run_trial`(launch + bag + meta)을 실행한다.
host 는 trial 순회 + sim 리셋만 책임 — 경계 분리(컨테이너=trial 로직, host=sim
라이프사이클).

## 2계층 (host venv 검증 / 컨테이너 실행)

| 계층 | 함수 | 의존성 | 검증 |
|---|---|---|---|
| **순수 코어** | `build_trial_from_coords` | host venv | ✅ pytest |
| **실행 셸** | `main` 의 `run_trial` 호출 | `launch` · sim 스택 | ⚠️ 컨테이너 |

좌표→TrialSpec 재구성이 `generate_trial_grid` 의 cell 구성과 *동일*함은
`grid.build_trial_spec` 단일 소스로 보장 — 단위 test 가 좌표 재구성 ↔ 격자 enumeration
의 seed·trial_id 일치를 cover.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from eval_baselines.schemas import BaselineMode
from eval_faults.fault_scenario import load_fault_scenario

from eval_runner.grid import build_trial_spec, resolve_fault_scenario_paths
from eval_runner.launch_composition import DEFAULT_BACKBONE
from eval_runner.runner import (
    DEFAULT_EPISODE_TIMEOUT_S,
    DEFAULT_OUTPUT_ROOT,
    trial_bag_dir,
)
from eval_runner.schemas import TrialSpec, VALID_SCENARIO_IDS


def build_trial_from_coords(
    scenario: str,
    baseline: str,
    fault: str,
    episode: int,
    confidence_source: str = 'live',
) -> TrialSpec:
    """trial 좌표 → TrialSpec (격자 enumeration 과 동일 — grid.build_trial_spec).

    Args:
        scenario: scenario_id (VALID_SCENARIO_IDS).
        baseline: BaselineMode value ('b0'..'b4').
        fault: fault scenario name (default 5종 — resolve_fault_scenario_paths).
        episode: episode_id (0 이상).
        confidence_source: 'live'(기본) 또는 'synthetic:<profile>' (ADR-0050 D7 안 B —
            합성 신뢰도 격리 격자). host plan(plan_to_json_obj)의 좌표를 그대로 재구성.

    Returns:
        TrialSpec — generate_trial_grid 가 같은 좌표에 대해 만드는 것과 동일(seed 포함).

    Raises:
        ValueError: baseline 무효 (BaselineMode), fault 무효 (resolve), scenario·
            episode·confidence_source 무효 (TrialSpec __post_init__).
        FileNotFoundError: fault YAML 부재.
    """
    mode = BaselineMode(baseline)  # 무효 측 ValueError
    fault_path = resolve_fault_scenario_paths([fault])[0]  # 무효 측 ValueError
    fault_scenario = load_fault_scenario(fault_path)
    return build_trial_spec(
        scenario, mode, fault_scenario, episode,
        confidence_source=confidence_source,
    )


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description='단일 trial 실행 (host-driven 오케스트레이션, ADR-0030 D5/D6). '
                    'host run_grid.py 가 좌표를 넘겨 컨테이너에서 호출.',
    )
    ap.add_argument('--scenario', required=True,
                    help=f'scenario_id {list(VALID_SCENARIO_IDS)}')
    ap.add_argument('--baseline', required=True, help="baseline mode ('b0'..'b4')")
    ap.add_argument('--fault', required=True, help='fault scenario name (default 5종)')
    ap.add_argument('--episode', type=int, required=True, help='episode_id (0 이상)')
    ap.add_argument('--confidence-source', default='live', dest='confidence_source',
                    help="신뢰도 소스 — 'live'(기본) 또는 'synthetic:<profile>' "
                         '(ADR-0050 D7 합성 신뢰도 격리 격자).')
    ap.add_argument('--output-root', default=DEFAULT_OUTPUT_ROOT, dest='output_root')
    ap.add_argument('--backbone', default=DEFAULT_BACKBONE,
                    help=f'intent_llm registry 식별자 (default {DEFAULT_BACKBONE})')
    ap.add_argument('--episode-timeout-s', type=float,
                    default=DEFAULT_EPISODE_TIMEOUT_S, dest='episode_timeout_s')
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """단일 trial 실행 — 좌표 → TrialSpec 재구성 → run_trial.

    ⚠️ `run_trial` 은 ROS 2 (`launch`) + sim 스택 의존 (컨테이너). host venv 측
    `build_trial_from_coords` 까지만 동작.
    """
    args = _parse_args(argv)
    trial = build_trial_from_coords(
        args.scenario, args.baseline, args.fault, args.episode,
        confidence_source=args.confidence_source,
    )
    bag_dir = trial_bag_dir(args.output_root, trial, args.backbone)
    # run_trial 은 ROS 2 의존 — lazy (module top-level import 회피, host venv 진입 보호).
    from eval_runner.runner import run_trial

    print(f'[run-one] {trial.trial_id} → {bag_dir}')
    wall = run_trial(trial, bag_dir, args.episode_timeout_s, args.backbone)
    print(f'[run-one] {trial.trial_id} done — wall={wall:.1f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
