#!/usr/bin/env python3
"""host-driven 본실험 격자 오케스트레이터 (ADR-0030 D5/D6).

★ 이것은 *단일 다리* 빌딩블록 — 인자대로 한 격자만 돈다(기본=통합 스택, `--track-b`=하한 검증).
   **"풀런"(양 다리 = 통합 스택 + Track B)은 `scripts/run_full_experiment.sh`** 로 — run_grid 직접
   호출만으로는 한쪽 다리(RQ1 누락 가능)다. 스크립트 정본 인덱스 = scripts/README.md.

영속 셸(`scripts/up.sh`)이 1회 기동된 상태에서, 본 스크립트가 **host 에서** 격자를
순회한다. trial 로직은 컨테이너에 위임하고(`docker exec eval-runner*`), sim
라이프사이클(trial 간 SITL+gz 리셋)만 host 가 책임진다 — 경계 분리(ADR-0030 D5).

흐름 (D6):

    docker exec eval-runner --plan-json <격자 args> --resume   # plan(resume 반영) → JSON
    for item in plan where status != 'done':
        docker exec eval-runner-one <item 좌표>                 # 컨테이너: 재구성 → run_trial
        scripts/sim_reset.sh                                    # host: kill→재기동→unpause→대기
    docker exec eval-runner --scan-bags                         # 무결성 게이트 (incomplete → exit 1)

좌표(scenario·baseline·fault name·episode)만으로 컨테이너가 동일 TrialSpec 을
재구성(seed 5차원 hash, 격자 순서 독립 — `grid.build_trial_spec`)하므로 TrialSpec
직렬화가 불필요하다.

⚠️ stdlib 전용 (host venv 에 eval_runner import 불요) — subprocess + json 만 사용.

전제:
  - `scripts/up.sh` 로 영속 셸 기동 (TIER1_MODE 미설정 — tier1 은 per-trial 합성 소유).
  - 컨테이너에 격자 패키지 colcon build 완료 (`--build` 로 1회 수행 가능). up.sh 기본
    빌드 목록엔 eval_*·intent_llm·intent_context·tier2_gate 가 없음 → 본실험 진입 전
    `--build` 필수.

종료 코드: 0 = 전 trial 실행 + 무결성 게이트 통과. 1 = 게이트 실패(incomplete 존재)
또는 plan 조회 실패.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_RESET = REPO_ROOT / 'scripts' / 'sim_reset.sh'

# per-trial wrapper(LLM backbone)에 전달할 환경변수 — cloud(gpt-4o) API 키 + edge
# (ollama) 주소. .env 에서 로드(미설정 시) 후 docker exec -e 로 컨테이너 자식에 전파.
_LLM_ENV_KEYS = ('OPENAI_API_KEY', 'OLLAMA_BASE_URL', 'ANTHROPIC_API_KEY')


def _load_dotenv() -> None:
    """REPO_ROOT/.env 의 LLM 키를 os.environ 에 채움(이미 있으면 미덮어씀)."""
    env_path = REPO_ROOT / '.env'
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        key = key.strip()
        if key in _LLM_ENV_KEYS and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")

# scenario_id → sim_reset SCENARIO(world). ADR-0006 + ADR-0039 D2: 거실 S5/S6 만
# (S7 폐기·S8 paper-2 이관). sim_reset.sh 가 SCENARIO 로 T1 SITL wrapper + gz world 선택.
SCENARIO_WORLD = {'S5': 'livingroom', 'S6': 'livingroom'}

# 격자 per-trial launch 가 요구하는 컨테이너 패키지 (up.sh 기본 목록 + eval_*·
# intent_llm·intent_context·tier2_gate). --build 시 colcon build 대상.
GRID_PACKAGES = (
    'scenario_params px4_msgs sim_user_marker g1_offboard tier1_filter '
    'intent_confidence intent_ovd waypoint_follower intent_llm intent_context '
    'tier2_gate eval_baselines eval_faults eval_runner'
)


def _dexec_prefix(container: str,
                  extra_env: Optional[dict] = None) -> List[str]:
    # LLM 키를 컨테이너 자식 프로세스(per-trial wrapper)에 전파 — host env 에 있는
    # 키만 -e 로(없으면 생략). gpt-4o 등 cloud backbone 의 API 키 필수.
    env_flags: List[str] = []
    for key in _LLM_ENV_KEYS:
        if os.environ.get(key):
            env_flags += ['-e', f'{key}={os.environ[key]}']
    # 호출자별 추가 env(예: run_one 의 TRIAL_LOG_DIR) — host env 가 아니라 명시값.
    for key, val in (extra_env or {}).items():
        env_flags += ['-e', f'{key}={val}']
    return ['docker', 'exec', *env_flags, container,
            '/usr/local/bin/entrypoint.sh', 'bash', '-c']


# 컨테이너 eval/faults 소스 디렉토리 — 콘솔 스크립트가 colcon install 트리에서
# 실행되므로 fault scenarios 경로를 명시(grid.ENV_FAULTS_ROOT, ADR-0030 D6 실측 발견).
_CONTAINER_FAULTS_ROOT = '/workspace/eval/faults'

# eval_calibration 은 colcon 패키지가 아니라 PYTHONPATH 로 import 하는 소스(package.xml
# 부재) — injector_node(hallucination.py)가 `eval_calibration.schemas` 를 import 하므로
# per-trial launch 의 자식 프로세스가 상속하도록 PYTHONPATH 에 추가(ADR-0030 D6 실측
# 발견). eval/baselines(launch/ 하위 → ROS launch shadow)는 *추가 금지* — calibration
# 만(launch 하위 없음 확인).
_CONTAINER_PYTHONPATH = '/workspace/eval/calibration'


def _sourced(cmd: str) -> str:
    return (
        f'cd /workspace && source install/setup.bash && '
        f'EVAL_FAULTS_ROOT={_CONTAINER_FAULTS_ROOT} '
        f'PYTHONPATH={_CONTAINER_PYTHONPATH}${{PYTHONPATH:+:$PYTHONPATH}} {cmd}'
    )


# ament_python 콘솔 스크립트는 PATH 에 안 올라가고 install/<pkg>/lib/<pkg>/ 에
# 설치 → `ros2 run <pkg> <exe>` 로 호출(ADR-0030 D6 실측 발견 — bare `eval-runner`
# command not found).
_RUN_PLAN = 'ros2 run eval_runner eval-runner'
_RUN_ONE = 'ros2 run eval_runner eval-runner-one'


def container_build(container: str) -> int:
    """격자 패키지 colcon build (컨테이너) — 본실험 진입 전 1회."""
    print(f'[run_grid] colcon build (격자 패키지) ...', flush=True)
    full = (
        f'cd /workspace && colcon build --packages-select {GRID_PACKAGES} '
        f'2>&1 | tail -5'
    )
    return subprocess.run(
        _dexec_prefix(container) + [full], check=False,
    ).returncode


def fetch_plan(container: str, grid_args: Sequence[str]) -> dict:
    """컨테이너 eval-runner --plan-json → plan dict (resume 반영)."""
    cmd = _sourced(f'{_RUN_PLAN} --plan-json --resume ' + ' '.join(grid_args))
    proc = subprocess.run(
        _dexec_prefix(container) + [cmd],
        check=False, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f'plan 조회 실패 (exit {proc.returncode})')
    # stdout 마지막 줄 = JSON (entrypoint.sh 가 source 로그를 앞에 찍을 수 있음).
    last = proc.stdout.strip().splitlines()[-1]
    return json.loads(last)


def run_one(container: str, item: dict, output_root: str, backbone: str,
            episode_timeout_s: float) -> int:
    """단일 trial 컨테이너 실행 (eval-runner-one). 출력은 host stdout 으로 상속."""
    one = (
        f"{_RUN_ONE} --scenario {item['scenario']} "
        f"--baseline {item['baseline']} --fault {item['fault']} "
        f"--episode {item['episode']} --output-root {output_root} "
        f"--backbone {backbone} --episode-timeout-s {episode_timeout_s}"
    )
    # ADR-0050 D7 — 합성 신뢰도 격리 셀은 confidence_source='synthetic:<profile>'.
    # plan(plan_to_json_obj)이 좌표로 방출 → 컨테이너가 동일 TrialSpec 재구성.
    # 'live'(기본)일 땐 부착하지 않는다 — eval-runner-one default 와 동일이라 live 런의
    # 명령줄이 이 기능 도입 전과 *바이트 동일*하게 유지된다(backward compat). 이러면
    # host `run_grid.py` 만 갱신되고 컨테이너가 stale(신규 `--confidence-source` 미지원)
    # 이어도 live 런은 안 깨진다. synthetic 경로는 fetch_plan 이 먼저 `--confidence-profiles`
    # 로 stale 을 조기 차단하고(eval-runner/-one 은 동일 colcon 패키지라 원자적 갱신),
    # 이때만 `--confidence-source` 를 전달한다.
    conf_src = item.get('confidence_source', 'live')
    if conf_src != 'live':
        one += f" --confidence-source {conf_src}"
    # ADR-0039 D3-②: per-trial wrapper(edge_llm/cloud_llm)의 LLM inference latency
    # JSONL 을 bag 과 같은 trial 출력 디렉터리에 남기도록 TRIAL_LOG_DIR 전파. 컨테이너
    # cwd=/workspace(repo 마운트)라 /workspace 하위 경로면 호스트에 그대로 남음.
    # _write_trial_log 가 makedirs(exist_ok) → 디렉터리 사전 생성 불요.
    trial_root = output_root if os.path.isabs(output_root) else f'/workspace/{output_root}'
    trial_log_dir = f"{trial_root}/{backbone}/{item['trial_id']}"
    extra_env = {'TRIAL_LOG_DIR': trial_log_dir}
    # ADR-0050 D2 제동 버퍼 실험 — host env 에 설정 시 컨테이너 per-trial launch
    # (compose_trial_node_specs)의 tier1 로 전파(미설정 시 off, 기존 거동).
    if os.environ.get('TIER1_BRAKE_BUFFER_M'):
        extra_env['TIER1_BRAKE_BUFFER_M'] = os.environ['TIER1_BRAKE_BUFFER_M']
    return subprocess.run(
        _dexec_prefix(container, extra_env) + [_sourced(one)],
        check=False,
    ).returncode


def sim_reset(scenario_id: str) -> int:
    """host sim 리셋 — scenario_id 에 맞는 world 로 SITL+gz 재기동."""
    world = SCENARIO_WORLD.get(scenario_id, 'livingroom')
    env = {**os.environ, 'SCENARIO': world}
    return subprocess.run([str(SIM_RESET)], env=env, check=False).returncode


def scan_gate(container: str, output_root: str, backbone: str) -> int:
    """무결성 게이트 — incomplete 존재 시 exit 1 (집계 진입 차단)."""
    cmd = _sourced(
        f'{_RUN_PLAN} --scan-bags --output-root {output_root} --backbone {backbone}'
    )
    return subprocess.run(_dexec_prefix(container) + [cmd], check=False).returncode


def _build_grid_args(args: argparse.Namespace) -> List[str]:
    parts: List[str] = []
    if args.scenarios:
        parts.append('--scenarios ' + ' '.join(args.scenarios))
    if args.baselines:
        parts.append('--baselines ' + ' '.join(args.baselines))
    if args.faults:
        parts.append('--faults ' + ' '.join(args.faults))
    if args.confidence_profiles:
        parts.append('--confidence-profiles ' + ' '.join(args.confidence_profiles))
    parts.append(f'--n-episodes {args.n_episodes}')
    parts.append(f'--output-root {args.output_root}')
    parts.append(f'--backbone {args.backbone}')
    return parts


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description='host-driven 본실험 격자 오케스트레이터 (ADR-0030 D5/D6).',
    )
    ap.add_argument('--scenarios', nargs='+', default=None,
                    help='scenario_id (default 컨테이너 전체)')
    ap.add_argument('--baselines', nargs='+', default=None,
                    help='baseline mode (default 컨테이너 전체)')
    ap.add_argument('--faults', nargs='+', default=None,
                    help='fault name (default 컨테이너 전체 5종)')
    ap.add_argument('--confidence-profiles', nargs='+', default=None,
                    dest='confidence_profiles',
                    help='합성 신뢰도 프로파일 name (ADR-0050 D7 격리 격자) — 격자를 '
                         '프로파일별로 확장. 미지정=live(현행). --track-b 와 결합해 '
                         '하한 검증 격자를 c≈1·중간·시변/정지로 격리 시험. '
                         '예: c_constant_1 c_constant_mid c_stall.')
    ap.add_argument('--n-episodes', type=int, default=10, dest='n_episodes')
    ap.add_argument('--output-root', default='results/trials', dest='output_root')
    ap.add_argument('--backbone', default='gemma-4-e4b')
    ap.add_argument('--episode-timeout-s', type=float, default=60.0,
                    dest='episode_timeout_s')
    ap.add_argument('--container', default=os.environ.get('CONTAINER_NAME', 'llmdrone-sim'))
    ap.add_argument('--build', action='store_true',
                    help='loop 진입 전 격자 패키지 colcon build (1회).')
    ap.add_argument('--no-reset', action='store_true', dest='no_reset',
                    help='trial 간 sim 리셋 생략 — *단일 sim 위 in-container smoke* '
                         '(리셋 없는 연속 trial, 재현성 미보장). 본실험 격자엔 사용 금지.')
    ap.add_argument('--dry-run', action='store_true', dest='dry_run',
                    help='plan 만 출력하고 실행 안 함 (host 측 점검).')
    ap.add_argument('--track-b', action='store_true', dest='track_b',
                    help='Track B sub-grid (ADR-0025 amendment 20) — 사용자 지향 적대 '
                         'setpoint × {b0,b1a,b1b,b2} × S5/S6 × 10 ep (80 trial). '
                         'scenarios/baselines/faults/output-root 미지정 시 Track B 기본값 '
                         '설정. ⚠️ 영속 셸을 SIGMA_STANDOFF=0 로 기동해야 함(D-T3) — '
                         'standoff 0.7 이면 침입이 r_min 경계에 취약.')
    return ap.parse_args(argv)


# 하한 검증 격자(Track B) sub-grid 기본값 (ADR-0025 amendment 20 D-T2 + ADR-0039 D2)
# — RQ1/C2-b 단조성-하한 시험. 거실 S5/S6 만(S7 폐기·S8 paper-2).
_TRACK_B_SCENARIOS = ['S5', 'S6']
_TRACK_B_BASELINES = ['b0', 'b1a', 'b1b', 'b2']
_TRACK_B_FAULT = 'hallucination_position_worst_user_direct'
_TRACK_B_OUTPUT_ROOT = 'results/track_b'


def _apply_track_b_defaults(args: argparse.Namespace) -> None:
    """--track-b 시 미지정 격자 인자를 Track B sub-grid 기본값으로 채운다."""
    if args.scenarios is None:
        args.scenarios = list(_TRACK_B_SCENARIOS)
    if args.baselines is None:
        args.baselines = list(_TRACK_B_BASELINES)
    if args.faults is None:
        args.faults = [_TRACK_B_FAULT]
    if args.output_root == 'results/trials':  # _parse_args default 미변경 시
        args.output_root = _TRACK_B_OUTPUT_ROOT


def _guard_python_interpreter() -> int:
    """anaconda/conda python 으로 실행되면 하드 에러 (F11).

    anaconda python 은 자손 프로세스의 gz 서버 dlopen 을 *비환경적*(dyld 수준)으로
    오염시켜 sim_reset 의 SITL 재기동을 결정적으로 실패시킨다(2026-06-14 실측 —
    "can't load libgz-sim8" / world timeout). 오염은 environ 에 없고(diff 결과 `_`
    제외 동일) execv·SIP 셸 재-exec 로도 안 끊겨 *자가 치유 불가*. .venv/system
    python 은 정상 → CLAUDE.md "Python 은 .venv 전용" 정책대로 .venv 실행을 강제한다.
    """
    exe = (sys.executable or '').lower()
    if 'anaconda' in exe or 'miniconda' in exe or 'conda' in exe:
        venv_py = REPO_ROOT / '.venv' / 'bin' / 'python3'
        sys.stderr.write(
            'ERROR: run_grid.py 가 anaconda/conda python 으로 실행됨 — sim_reset 의 gz\n'
            '       서버 재기동이 실패한다(F11; dyld 오염은 재-exec 로 안 풀림).\n'
            '       프로젝트 .venv 로 실행하세요:\n'
            f'         {venv_py} scripts/run_grid.py ...\n'
        )
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    if _guard_python_interpreter() != 0:
        return 1
    args = _parse_args(argv)
    if args.track_b:
        _apply_track_b_defaults(args)
        print(
            '[run_grid] Track B sub-grid (amendment 20) — 사용자 지향 적대 setpoint.\n'
            '           ⚠️ 영속 셸이 SIGMA_STANDOFF=0 로 기동됐는지 확인 '
            '(standoff 0.7 이면 침입 취약, D-T3).',
            flush=True,
        )
    container = args.container
    _load_dotenv()  # LLM 키를 os.environ 에 (docker exec -e 전파용)

    if not SIM_RESET.is_file():
        sys.stderr.write(f'ERROR: sim_reset.sh 미발견 — {SIM_RESET}\n')
        return 1

    if args.build:
        # --dry-run 과 병행 가능 — 빌드(idempotent) + 계획 미리보기로 trial 실행
        # 직전까지 전부 검증(컨테이너 격자 패키지 빌드 + plan-json fetch).
        rc = container_build(container)
        if rc != 0:
            sys.stderr.write(f'WARN: colcon build exit {rc} — 로그 확인.\n')

    grid_args = _build_grid_args(args)
    plan = fetch_plan(container, grid_args)
    trials = plan['trials']
    todo = [t for t in trials if t['status'] != 'done']
    print(f'[run_grid] 총 {len(trials)} trial — 실행 대상 {len(todo)} '
          f'(done {len(trials) - len(todo)} 건너뜀)', flush=True)

    if args.dry_run:
        for t in todo:
            print(f"  [{t['status']:10}] {t['trial_id']}")
        return 0

    for idx, item in enumerate(todo):
        print(f"\n[run_grid] ({idx + 1}/{len(todo)}) {item['trial_id']}", flush=True)
        rc = run_one(container, item, args.output_root, args.backbone,
                     args.episode_timeout_s)
        if rc != 0:
            sys.stderr.write(
                f"[run_grid] WARN: {item['trial_id']} exit {rc} — "
                f"resume/scan 이 incomplete 로 포착.\n"
            )
        # 다음 trial 을 위해 sim 리셋 (마지막 trial 뒤엔 생략 — 다음 없음).
        is_last = idx == len(todo) - 1
        if not args.no_reset and not is_last:
            print(f"[run_grid] sim 리셋 ({item['scenario']}) ...", flush=True)
            rrc = sim_reset(item['scenario'])
            if rrc != 0:
                sys.stderr.write(
                    f"[run_grid] WARN: sim_reset exit {rrc} — 다음 trial 위험. "
                    f"중단 권장.\n"
                )

    print('\n[run_grid] 무결성 게이트 (--scan-bags) ...', flush=True)
    gate = scan_gate(container, args.output_root, args.backbone)
    print(f'[run_grid] 완료 — 게이트 exit {gate}', flush=True)
    return gate


if __name__ == '__main__':
    sys.exit(main())
