"""격자 enumeration — ADR-0025 D3 + amendment 19 1200 trial 생성.

[ADR-0025 D3](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d3)
격자 (amendment 19 — baseline 5→6, B1→B1a/B1b):

    N_trial = |scenarios (4)| × |baselines (6)| × |fault_class (5)| × N_episode (10)
            = 4 × 6 × 5 × 10
            = 1,200 trial

본 모듈 = host venv 측 *pure-Python* cartesian product enumeration. ROS 2
launch composition 측 후속 PR (B7 #12 분할 2/N) 측 입력 list.

호출 패턴:

    scenarios = ['S5', 'S6']
    baseline_modes = list(BaselineMode)
    fault_paths = sorted(Path('eval/faults/scenarios').glob('*.yaml'))
    grid = generate_trial_grid(scenarios, baseline_modes, fault_paths, n_episodes=10)
    assert len(grid) == 600

    check_chain_invariant(grid)  # ablation_invariant.py 측 자동 검증

각 TrialSpec 측 seed = seed_policy.derive_trial_seed(5-tuple) — 격자 enumeration
순서 측 *독립* (= 격자 차원 추가/제거 측 seed shift 회피).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# fault scenarios 디렉토리 override — colcon install 트리에서 실행 시
# Path(__file__) 상대 heuristic 이 install/.../python3.10/faults 를 가리켜 소스
# (eval/faults)를 못 찾는다(host venv 는 소스 트리라 무관). 컨테이너 run_grid.py
# 가 EVAL_FAULTS_ROOT=/workspace/eval/faults 로 설정(ADR-0030 D6 실 sim 검증에서
# 발견). 설정 시 default_fault_scenario_paths(root=None) 가 이 값을 우선한다.
ENV_FAULTS_ROOT = 'EVAL_FAULTS_ROOT'

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.b3_context_aug import b3_config
from eval_baselines.b4_full_loop import b4_config
from eval_baselines.schemas import BaselineConfig, BaselineMode
from eval_faults.fault_scenario import FaultScenario, load_fault_scenario

from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec
from eval_runner.seed_policy import derive_trial_seed


# BaselineMode ↔ helper 매핑 잠금 — runner.py 측 b{N}_config() helper 직접 import
# 측 *single source-of-truth* (eval_baselines 패키지). mode ↔ 3 축 매핑 측 본
# helper 측 잠금이라 BaselineConfig 측 *내부 일관성* 자동 보장.
BASELINE_HELPERS: Dict[BaselineMode, Callable[[], BaselineConfig]] = {
    BaselineMode.B0: b0_config,
    BaselineMode.B1A: b1a_config,
    BaselineMode.B1B: b1b_config,
    BaselineMode.B2: b2_config,
    BaselineMode.B3: b3_config,
    BaselineMode.B4: b4_config,
}

# seed 정규화 (ADR-0025 amendment 19) — B1a 는 종전 B1(정적 $r_\\text{min}$)과 동일
# 거동이므로 seed 차원을 'b1' 로 정규화해 기존 B1 trial seed·재현성을 보존한다.
# B1b 는 신규(정적 $r_\\text{max}$) — 자기 value 'b1b' 그대로 새 seed. 나머지 불변.
_SEED_BASELINE_NORMALIZE: Dict[str, str] = {
    BaselineMode.B1A.value: 'b1',
}


def build_trial_spec(
    scenario_id: str,
    mode: BaselineMode,
    fault_scenario: FaultScenario,
    episode_id: int,
    confidence_source: str = 'live',
) -> TrialSpec:
    """단일 cell → TrialSpec — generate_trial_grid 의 cell 구성과 *동일* 로직.

    seed = derive_trial_seed(5-tuple) — 격자 enumeration 순서 *독립* (본 모듈
    docstring Note). host-driven 오케스트레이션(ADR-0030 D5)의 `eval-runner-one`
    이 좌표(scenario·baseline·fault·episode)만으로 *동일* TrialSpec 을 재구성할 때
    본 helper 를 재사용 → generate_trial_grid 와의 drift 원천 차단(seed·baseline_config
    구성이 한 곳).

    Args:
        scenario_id: VALID_SCENARIO_IDS 중 하나.
        mode: BaselineMode — BASELINE_HELPERS key.
        fault_scenario: load_fault_scenario 결과.
        episode_id: 0 이상.
        confidence_source: 'live'(기본) 또는 'synthetic:<profile>' (ADR-0050 D7 안 B —
            합성 신뢰도 격리). seed 에는 미포함 — 프로파일 간 *동일 fault 실현*을
            유지(신뢰도만 변주)하고, trial_id 접미로 충돌만 방지.

    Returns:
        TrialSpec — generate_trial_grid 가 같은 5차원에 대해 만드는 것과 동일.

    Raises:
        KeyError: mode 측 BASELINE_HELPERS 외 (정상 BaselineMode 측 unreachable).
        ValueError/TypeError: TrialSpec __post_init__ 검증 (scenario_id·episode_id·seed·
            confidence_source).
    """
    seed = derive_trial_seed(
        scenario_id=scenario_id,
        baseline_mode=_SEED_BASELINE_NORMALIZE.get(mode.value, mode.value),
        fault_channel=fault_scenario.channel.value,
        fault_variant=fault_scenario.variant,
        episode_id=episode_id,
    )
    return TrialSpec(
        scenario_id=scenario_id,
        baseline_config=BASELINE_HELPERS[mode](),
        fault_scenario=fault_scenario,
        episode_id=episode_id,
        seed=seed,
        confidence_source=confidence_source,
    )


def generate_trial_grid(
    scenarios: Sequence[str],
    baseline_modes: Sequence[BaselineMode],
    fault_scenario_paths: Sequence[Union[str, Path]],
    n_episodes: int,
) -> List[TrialSpec]:
    """ADR-0025 D3 격자 cartesian product → list[TrialSpec].

    enumeration 순서: scenarios → baseline_modes → fault_scenarios → episodes.
    본 순서 측 ablation_invariant.check_chain_invariant 측 groupby 패턴 정합
    (동일 scenario·fault·episode 측 baseline 6 종 인접).

    Args:
        scenarios: 시나리오 식별자 list — VALID_SCENARIO_IDS 부분집합.
        baseline_modes: BaselineMode list — BASELINE_HELPERS 측 key.
        fault_scenario_paths: fault YAML 파일 경로 list — load_fault_scenario
            측 입력. 보통 ``sorted(Path('eval/faults/scenarios').glob('*.yaml'))``.
        n_episodes: 각 (scenario · baseline · fault) cell 측 반복 수 (ADR-0025
            D3 N=10 1차 시안).

    Returns:
        list[TrialSpec] — len = |scenarios| × |baseline_modes| × |fault_scenario_paths|
        × n_episodes. ADR-0025 D3 + ADR-0039 D2 잠금 격자 측 default 호출 측 600 trial (거실 S5/S6).

    Raises:
        ValueError: scenarios 빈 list, n_episodes ≤ 0, scenarios 측 VALID_SCENARIO_IDS
            외 값, baseline_modes 측 BASELINE_HELPERS 외 key.
        FileNotFoundError: fault_scenario_paths 측 파일 부재 (load_fault_scenario
            측 propagate).

    Note:
        본 함수 측 격자 *순서 의존* X — TrialSpec 측 seed 가 5-tuple 측
        deterministic hash. 격자 enumeration 순서 변경 측 동일 seed 보장 (단,
        반환 list 순서 만 변동).
    """
    if not scenarios:
        raise ValueError('scenarios 빈 list 불가 — ADR-0025 D3 최소 1 시나리오')
    if not baseline_modes:
        raise ValueError('baseline_modes 빈 list 불가 — ADR-0025 D3 최소 1 baseline')
    if not fault_scenario_paths:
        raise ValueError(
            'fault_scenario_paths 빈 list 불가 — ADR-0025 D3 최소 1 fault_class'
        )
    if not isinstance(n_episodes, int) or isinstance(n_episodes, bool):
        raise TypeError(
            f'n_episodes 는 int 여야 함, got {type(n_episodes).__name__}'
        )
    if n_episodes <= 0:
        raise ValueError(f'n_episodes={n_episodes} 무효 — 1 이상 필수')

    for sid in scenarios:
        if sid not in VALID_SCENARIO_IDS:
            raise ValueError(
                f'scenario_id={sid!r} 무효 — {VALID_SCENARIO_IDS} 중 하나여야 함'
            )
    for mode in baseline_modes:
        if mode not in BASELINE_HELPERS:
            raise ValueError(
                f'baseline_mode={mode!r} 무효 — '
                f'{sorted(m.value for m in BASELINE_HELPERS)} 중 하나여야 함'
            )

    fault_scenarios: List[FaultScenario] = [
        load_fault_scenario(p) for p in fault_scenario_paths
    ]

    # PR #121 self-review M-1 정정 — (channel, variant) 4-tuple distinct 검증.
    # ablation_invariant.py 측 cell_key 가 (scenario, channel, variant, episode)
    # 4-tuple 측 동일 (channel, variant) 두 YAML 측 *silent merging* (다른
    # context_kwargs 측 같은 cell 측 추가) 차단. ADR-0025 D5 #5a 5 fault YAML
    # 측 channel+variant distinct 잠금 정합 — 후속 YAML 추가 측 collision 측
    # 조기 차단.
    seen_keys: Dict[Tuple[str, Optional[str]], FaultScenario] = {}
    for fs in fault_scenarios:
        key = (fs.channel.value, fs.variant)
        if key in seen_keys:
            raise ValueError(
                f'fault YAML 측 (channel, variant) 중복 — '
                f'{key!r} = {seen_keys[key].name!r} ↔ {fs.name!r}. '
                f'ablation_invariant.cell_key 측 ambiguity 차단 위해 '
                f'(channel, variant) distinct 필수.'
            )
        seen_keys[key] = fs

    grid: List[TrialSpec] = []
    for scenario_id in scenarios:
        for mode in baseline_modes:
            for fault_scenario in fault_scenarios:
                for episode_id in range(n_episodes):
                    grid.append(build_trial_spec(
                        scenario_id, mode, fault_scenario, episode_id,
                    ))
    return grid


def resolve_fault_scenario_paths(
    names: Sequence[str],
    root: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """fault YAML *name* list → path list (name 매칭, 단일 출처).

    runner.py (격자 실행) + experiment_panel.py (웹 패널) 측 공통 resolver —
    YAML 의 ``name`` 필드 (FaultScenario.name) 로 default 5종 중 선택.

    Args:
        names: fault scenario name list (FaultScenario.name 값).
        root: eval/faults/ 디렉토리. None 측 default_fault_scenario_paths 정합.

    Returns:
        list[Path] — names 순서 유지.

    Raises:
        ValueError: names 측 default 5종에 없는 name.
    """
    # 넓은 격자 5종 + Track B(track_b/, ADR-0028·amendment 20) name 모두 해석 가능.
    # 넓은 격자 *enumeration* (default_fault_scenario_paths)은 불변 — track_b 는 name
    # 해석 registry 에만 추가(broad grid 오염 없음).
    registry: Dict[str, Path] = {
        load_fault_scenario(p).name: p
        for p in (*default_fault_scenario_paths(root), *track_b_fault_scenario_paths(root))
    }
    out: List[Path] = []
    for name in names:
        if name not in registry:
            raise ValueError(
                f'fault={name!r} 무효 — {sorted(registry)} 중 하나여야 함'
            )
        out.append(registry[name])
    return out


def default_fault_scenario_paths(
    root: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """eval/faults/scenarios/ 측 5 YAML 자동 도출.

    [ADR-0025 D5 #5a](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
    측 5 시나리오 (none + 4 channel) 잠금 정합 — root/scenarios/*.yaml glob 측
    sorted list.

    Args:
        root: eval/faults/ 디렉토리 경로. None 측 본 모듈 측 sibling 경로
            (eval/faults/) 자동 도출.

    Returns:
        list[Path] — sorted (deterministic enumeration 측). ADR-0025 D5 잠금
        측 5 YAML.

    Raises:
        FileNotFoundError: scenarios/ 디렉토리 부재 또는 빈 디렉토리.
    """
    if root is None:
        env_root = os.environ.get(ENV_FAULTS_ROOT)
        if env_root:
            # 컨테이너 colcon install 실행 경로 — install 트리 상대 heuristic 회피.
            root = Path(env_root)
        else:
            # Path(__file__).resolve() = eval/runner/eval_runner/grid.py
            # parents[0]=eval_runner/, parents[1]=runner/, parents[2]=eval/.
            root = Path(__file__).resolve().parents[2] / 'faults'
    root = Path(root)
    scenarios_dir = root / 'scenarios'
    if not scenarios_dir.is_dir():
        raise FileNotFoundError(
            f'fault scenarios 디렉토리 부재 — {scenarios_dir}'
        )
    paths = sorted(scenarios_dir.glob('*.yaml'))
    if not paths:
        raise FileNotFoundError(
            f'fault scenarios YAML 부재 — {scenarios_dir} 측 *.yaml 0 건'
        )
    return paths


def track_b_fault_scenario_paths(
    root: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """eval/faults/scenarios/track_b/ 의 YAML (ADR-0028 Track B · amendment 20).

    넓은 격자(default_fault_scenario_paths, scenarios/*.yaml glob)와 *분리* 보존 —
    Track B 사용자 지향 적대 변형(position_worst_user_direct)은 별 sub-grid(baseline
    4종 × S5/S6)로 실행한다. name 해석(resolve_fault_scenario_paths)이 본 경로도
    포함해 `--faults <track_b name>` 이 동작한다.

    Args:
        root: eval/faults/ 디렉토리. None 측 default_fault_scenario_paths 와 동일 도출.

    Returns:
        list[Path] — sorted. track_b/ 부재 시 빈 list(넓은 격자만 설치된 환경).
    """
    if root is None:
        env_root = os.environ.get(ENV_FAULTS_ROOT)
        if env_root:
            root = Path(env_root)
        else:
            root = Path(__file__).resolve().parents[2] / 'faults'
    track_b_dir = Path(root) / 'scenarios' / 'track_b'
    if not track_b_dir.is_dir():
        return []
    return sorted(track_b_dir.glob('*.yaml'))
