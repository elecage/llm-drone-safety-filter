"""eval_runner schemas — TrialSpec frozen dataclass.

[ADR-0025 D3](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d3)
격자 측 *단일 trial* 의 *완전한 사양* 잠금. 격자 차원 5 종 (scenario · baseline ·
fault_class · fault_variant · episode) 의 cartesian product 측 단일 cell 측 정의.

각 TrialSpec 은 후속 PR (B7 #12 분할 2/N) 측 ROS 2 launch composition logic 의
*단일 입력* — runner.py 가 격자 enumeration 후 각 TrialSpec 별 launch description
합성 + rosbag2 record + trial_meta.yaml 출력.

구성 요소:
  - scenario_id: 시나리오 식별자 ([ADR-0006](../../../docs/handover/decisions/0006-paper1-scenario-set.md)
    + [ADR-0026 D6](../../../docs/handover/decisions/0026-paper1-perception-assumptions.md#d6)
    indoor 2 종 S5/S6, ADR-0039 D2).
  - baseline_config: [eval_baselines.schemas.BaselineConfig](../../baselines/eval_baselines/schemas.py)
    — b{N}_config() helper 측 도출. mode·tier1_mode·context_aug·tier2_enabled 4 필드.
  - fault_scenario: [eval_faults.fault_scenario.FaultScenario](../../faults/eval_faults/fault_scenario.py)
    — load_fault_scenario() 측 도출. channel·variant·context_kwargs·seed 4 필드.
  - episode_id: 0 to n_episodes-1 (ADR-0025 D3 N=10 1차 시안).
  - seed: 5 차원 deterministic hash (seed_policy.py 측 도출) — fault hook 측
    random.Random(seed) 입력. trial 재현성 보장.

본 dataclass 의 *internal consistency* 측 책임 분리:
  - baseline_config 측 3 축 ↔ mode 매핑 = b{N}_config() helper 잠금 (BaselineConfig
    측 검증 안 함, eval_baselines.schemas 정합).
  - fault_scenario 측 channel ↔ variant ↔ context_kwargs 매핑 = FaultScenario
    __post_init__ 측 검증 (eval_faults.fault_scenario 정합).
  - 본 TrialSpec __post_init__ = *5 차원 사이 정합* 만 검증 (scenario_id ∈ 2
    종 + episode_id ≥ 0 + seed ≥ 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from eval_baselines.schemas import BaselineConfig
from eval_faults.fault_scenario import FaultScenario


# ADR-0006 메인 시나리오 중 거실 S5/S6 만 — ADR-0026 D6 (paper §C 시뮬 indoor
# 한정) + ADR-0039 D2 (S7 폐기·S8 paper-2 이관, 시나리오 축 = C2 신뢰도 스펙트럼
# 전용) 정합. scenario_params.VALID_SCENARIO_IDS 와 일치 (panel.py 가드 검증).
VALID_SCENARIO_IDS: Tuple[str, ...] = ('S5', 'S6')


@dataclass(frozen=True)
class TrialSpec:
    """단일 trial 의 *완전한 사양* 잠금 — ADR-0025 D3 격자 측 단일 cell.

    Attributes
    ----------
    scenario_id : str
        ADR-0006 indoor 2 시나리오 식별자 — 'S5' | 'S6' (ADR-0039 D2).
    baseline_config : BaselineConfig
        eval_baselines.schemas 측 baseline 사양 — b{N}_config() helper 측 도출.
    fault_scenario : FaultScenario
        eval_faults.fault_scenario 측 fault 사양 — load_fault_scenario(path) 측
        도출. channel='none' 측 baseline (no transformation) trial 표현.
    episode_id : int
        0 to n_episodes-1 (ADR-0025 D3 N=10 1차 시안). 동일 (scenario · baseline
        · fault) cell 측 N 번 반복 — CI 폭 추정 기반.
    seed : int
        5 차원 deterministic hash (seed_policy.derive_trial_seed) 측 도출.
        fault hook 측 random.Random(seed) 입력 — trial 재현성 보장. uint32
        범위 ([0, 2**32 - 1]).
    """

    scenario_id: str
    baseline_config: BaselineConfig
    fault_scenario: FaultScenario
    episode_id: int
    seed: int

    def __post_init__(self) -> None:
        if self.scenario_id not in VALID_SCENARIO_IDS:
            raise ValueError(
                f'scenario_id={self.scenario_id!r} 무효 — '
                f'{VALID_SCENARIO_IDS} 중 하나여야 함 '
                f'(ADR-0006 indoor 4 + ADR-0026 D6 + ADR-0025 amendment 7)'
            )
        if not isinstance(self.episode_id, int) or isinstance(self.episode_id, bool):
            raise TypeError(
                f'episode_id 는 int 여야 함, '
                f'got {type(self.episode_id).__name__}'
            )
        if self.episode_id < 0:
            raise ValueError(
                f'episode_id={self.episode_id} 무효 — 0 이상 필수'
            )
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError(
                f'seed 는 int 여야 함, got {type(self.seed).__name__}'
            )
        if self.seed < 0 or self.seed >= 2**32:
            raise ValueError(
                f'seed={self.seed} 범위 위반 — [0, 2**32) 필수 (uint32)'
            )

    @property
    def trial_id(self) -> str:
        """trial 식별자 string — rosbag2 파일명·trial_meta.yaml 키 측 사용.

        형식: ``{scenario_id}__{baseline_mode}__{fault_channel}__{fault_variant_or_none}__ep{episode_id:02d}``

        예시:
            'S5__b2__hallucination__position_gauss_low__ep00'
            'S6__b0__none__none__ep09'

        본 식별자가 격자 측 *unique* 보장 — 동일 trial_id 측 두 TrialSpec 측
        다른 seed 측 불가능 (seed 가 5 차원 hash 측 deterministic).
        """
        variant = self.fault_scenario.variant or 'none'
        return (
            f'{self.scenario_id}__'
            f'{self.baseline_config.mode.value}__'
            f'{self.fault_scenario.channel.value}__'
            f'{variant}__'
            f'ep{self.episode_id:02d}'
        )
