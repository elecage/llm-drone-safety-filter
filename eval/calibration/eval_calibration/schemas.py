"""Calibration dataclasses — paper §C 부록 보고 YAML schema.

ADR-0025 D1.b 의 sigma_llm_nat / target_swap_rate / unrelated_sigma_rate 3 측정값을
백본 × 시나리오 단위로 보존.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Backbone(str, Enum):
    """ADR-0025 D1.b amendment 8 — calibration 대상 cloud LLM 2종 (세대 양 끝점)."""

    GPT_4O = 'gpt-4o-2024-05-13'   # ADR-0014 D1 #6, 2024
    GPT_5_5 = 'gpt-5.5'            # ADR-0014 D1 #2, 2026 (잠정 식별자)


@dataclass(frozen=True)
class TypedAction:
    """ADR-0013 D2 의 5 스킬 카탈로그 σ = (sigma, theta) 직렬화.

    sigma ∈ {move_to, inspect, return_to_dock, emergency_land, ask_user}.
    theta 는 sigma 별 다름 (ADR-0013 D2 validator).
    """

    sigma: str
    theta: Dict[str, Any]

    def __post_init__(self) -> None:
        allowed = {'move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'}
        if self.sigma not in allowed:
            raise ValueError(f'sigma "{self.sigma}" not in ADR-0013 D2 catalog {allowed}')


@dataclass(frozen=True)
class ScenarioSpec:
    """정상 사용자 prompt + expected nominal σ.

    expected_action 은 시나리오의 *대표 정답* (모호 referent 처럼 모호한 경우 None).
    measure.py 가 LLM 출력 vs expected_action 비교로 σ_LLM,nat 계산.
    """

    scenario_id: str            # 'S5', 'S6', 'S7', 'S8'
    description: str
    user_prompt: str
    expected_action: Optional[TypedAction] = None
    expected_position: Optional[Tuple[float, float, float]] = None  # m, local frame
    expected_target_id: Optional[str] = None
    known_objects: List[str] = field(default_factory=list)          # SDF catalog
    # ADR-0025 amendment 12 (D1.e) — context-provided 조건용 객체 좌표 (world frame).
    # {name: (x, y, z)}. 비어 있으면 context-provided calibration 불가 (context-absent만).
    known_object_positions: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)
    # ADR-0025 amendment 13 — positional σ 측정용 move_to-natural probe 발화.
    # 시나리오 정상 user_prompt 가 inspect/ask_user 유발 시 positional σ 측정 불가
    # (S6 "보여줘"→inspect). 각 probe = {'prompt': move_to 유발 발화,
    # 'expected_object': known_object_positions 의 키(xy 기준 referent)}.
    move_to_probes: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class SampleOutput:
    """단일 LLM 호출 결과 — sample 50 회 중 1 회.

    deltas:
      - position_xyz_cm: |θ_LLM.position - expected_position| 의 L2 norm (cm)
                          expected_position is None 이면 NaN
      - is_swap: bool, target_id 변경 여부 (expected_target_id 와 다름)
      - is_unrelated: bool, expected 와 무관 σ 발화 (예 ask_user 인데 expected = inspect)
    """

    prompt: str
    sigma: TypedAction
    expected_action: Optional[TypedAction]
    deltas: Dict[str, Any]


@dataclass
class SigmaLlmNat:
    """ADR-0025 D1.b 의 4 측정값 — 백본 × 시나리오 단위.

    PR #82 review C1 amendment: `no_call_rate` 추가 (NATURAL 모드의 자연
    fail-gracefully 측정). `unrelated_sigma_rate` 는 ambiguous (expected=ask_user)
    시나리오에서 NaN (C3 amendment).

    paper §C fault_variant Gaussian σ mapping:
      - gauss_low = 1 × position_xyz_cm
      - gauss_med = 5 × position_xyz_cm
    """

    position_xyz_cm: float           # std of |θ_LLM - θ_normal| (cm)
    target_swap_rate: float          # 0-1
    unrelated_sigma_rate: float      # 0-1 또는 NaN (ambiguous 시나리오)
    no_call_rate: float = 0.0        # 0-1, function call 회피 비율 (NATURAL 모드)


@dataclass
class CalibrationResult:
    """한 (백본, 시나리오) 의 측정 산출물 — YAML 직렬화 대상."""

    backbone: str                    # Backbone enum value
    scenario: str                    # 'S5', 'S6', ...
    n_samples: int
    timestamp: str                   # ISO 8601 UTC
    sigma_llm_nat: SigmaLlmNat
    samples: List[SampleOutput] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """YAML dump 용 dict 변환 (dataclass nested → dict)."""
        return asdict(self)


# ─── probe 기반 positional σ 두 조건 측정 (ADR-0025 amend 12/13) ──────────────
# 시나리오 정상 user_prompt 의 거동 분포(SigmaLlmNat) 와 별개로, move_to-natural
# probe 발화를 context-provided / context-absent 두 조건으로 측정해 referent 좌표
# 환각(positional σ)을 축별로 분해한다. 기둥①(context augmentation) 효과 정량.


@dataclass
class ProbeConditionMeasurement:
    """한 (probe, 조건) 의 measurement.

    context_provided=True 면 known_object_positions 좌표를 LLM 에 노출(본실험
    fusion 정합, σ≈0 예상), False 면 이름만(LLM 좌표 추측 → σ 측정).
    axis_sigma_cm / axis_mean_m 은 compute_axis_sigma 출력의 평탄화 (x/y/z).
    """

    context_provided: bool
    n_samples: int                   # 조건당 호출 수
    n_move_to: int                   # σ 계산 표본 수 (position 이 3-tuple 로 파싱된
                                     # move_to). ≤ skill_distribution['move_to']
    skill_distribution: Dict[str, int]   # LLM 거동 분포 {sigma|'(no_call)': count}, 합=n_samples
    axis_sigma_cm: Dict[str, float]      # {'x', 'y', 'z'} (NaN 가능)
    axis_mean_m: Dict[str, float]        # {'x', 'y', 'z'} (NaN 가능)


@dataclass
class ProbeMeasurement:
    """한 probe 발화의 두 조건(provided/absent) 대조."""

    prompt: str
    expected_object: str
    expected_xy: Optional[Tuple[float, float]]   # known_object_positions[obj] 의 xy
    provided: ProbeConditionMeasurement
    absent: ProbeConditionMeasurement


@dataclass
class ProbeCalibrationResult:
    """한 (백본, 시나리오) 의 probe positional σ 산출물 — YAML 직렬화 대상."""

    backbone: str                    # Backbone enum value
    scenario: str                    # 'S5', 'S6', ...
    n_samples: int                   # 조건당 호출 수 (probe·조건 공통)
    timestamp: str                   # ISO 8601 UTC
    probes: List[ProbeMeasurement] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """YAML dump 용 dict 변환 (dataclass nested → dict)."""
        return asdict(self)
