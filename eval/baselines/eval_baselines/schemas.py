"""eval_baselines schemas — BaselineMode enum + BaselineConfig dataclass.

ADR-0025 D3 격자의 6 baseline (B0·B1a·B1b·B2·B3·B4) 정의 잠금. 각 baseline 은 3
축으로 분해 가능:

  1. tier1_mode: tier1_filter 측 CBF-QP 모드 ('b0' | 'b1' | 'b1_max' | 'b2')
     - b0 = 필터 없음 (pass-through)
     - b1 = 정적 $r_\\text{min}$ CBF-QP (B1a, 효율 baseline)
     - b1_max = 정적 $r_\\text{max}$ CBF-QP (B1b, 안전 baseline)
     - b2 = 신뢰도 변조 $r(\\tilde c)$ CBF-QP
  2. context_aug: intent layer 측 context augmentation 활성화 여부
     - False = LLM 측 사용자 prompt 만 입력 (정상 baseline)
     - True  = context graph + ego-stream 융합 (paper §6 fusion)
  3. tier2_enabled: Tier 2 런타임 검증 게이트 활성화 여부
     - False = Tier 2 우회 (LLM 출력 σ 직접 통과)
     - True  = Tier 2 시간논리 사양 (Temporal Spec) $\\Phi_1$·$\\Phi_8$·$\\Phi_9$·$\\Phi_{10}$
       등 강제. ADR-0025 D3 표 line 310 측 "TTS" 표현 정합 — TTS 약어는 Text-To-Speech
       와 충돌 가능하므로 본 코드에서는 풀어 씀 (CLAUDE.md A2 약어 충돌 회피).

paper §C 6-way ablation 의 *3 축 구조* (ADR-0025 amendment 19 — B1→B1a/B1b 분리):

| baseline | tier1_mode | context_aug | tier2_enabled |
|---|---|---|---|
| B0  | b0     | False | False |
| B1a | b1     | False | False |
| B1b | b1_max | False | False |
| B2  | b2     | False | False |
| B3  | b2     | True  | False |
| B4  | b2     | True  | True  |

B0/B1a/B1b/B2 차이 = tier1_mode 단독, B2/B3 차이 = context_aug 단독, B3/B4 차이 =
tier2_enabled 단독. *축 별 효과 분리* 가 ablation 의 의미 — paper §C 표 1 에서
각 행 사이 단일 축 변경 만으로 효과 측정 가능.

B1a(정적 $r_\\text{min}$)·B1b(정적 $r_\\text{max}$)는 트레이드오프 곡선의 효율점·
안전점 두 끝 — B2(변조)가 둘을 모두 dominate 함이 C2(C4) 입증.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class BaselineMode(str, Enum):
    """paper §C 6-way ablation baseline 6 종 (ADR-0025 D3 + amendment 19).

    amendment 19: 종전 B1(정적 마진)을 B1a(정적 $r_\\text{min}$, 효율)·B1b(정적
    $r_\\text{max}$, 안전)로 분리 — 트레이드오프 곡선의 두 끝을 baseline 으로 둠.
    """

    B0 = 'b0'
    B1A = 'b1a'
    B1B = 'b1b'
    B2 = 'b2'
    B3 = 'b3'
    B4 = 'b4'


_VALID_TIER1_MODES: Tuple[str, ...] = ('b0', 'b1', 'b1_max', 'b2')


@dataclass(frozen=True)
class BaselineConfig:
    """단일 baseline 의 *trial-level 구성* 잠금 dataclass.

    runner.py 가 본 dataclass 를 입력 받아 (a) tier1_filter mode 파라미터 잠금,
    (b) intent layer 노드 launch 여부 결정, (c) Tier 2 노드 launch 여부 결정.

    Attributes
    ----------
    mode : BaselineMode
        baseline 식별자 (B0·B1a·B1b·B2·B3·B4).
    tier1_mode : str
        tier1_filter 의 mode 파라미터 — 'b0' | 'b1' | 'b1_max' | 'b2' 중 하나.
        tier1_filter.filter_node.FilterMode 와 정합.
    context_aug : bool
        intent layer 측 context augmentation 활성화 여부. False 면 LLM 입력 =
        사용자 prompt 만, True 면 context graph + ego-stream 융합.
    tier2_enabled : bool
        Tier 2 런타임 검증 게이트 활성화 여부. False 면 LLM 출력 σ 가 Tier 2
        우회하고 tier1 으로 직행, True 면 Tier 2 시간논리 사양 (Temporal Spec)
        강제.

    Note: 본 dataclass 의 *mode 필드는 식별자 label* 일 뿐이며, 실 trial 구성은
    *3 축 (tier1_mode · context_aug · tier2_enabled)* 이 결정. `mode=B0` 인데
    `tier1_mode='b2'` 같은 *내부 불일치* 조합은 본 dataclass 측 검증 안 함 —
    mode ↔ 3 축 매핑은 `b{N}_config()` helper (b0_passthrough / b1a_static_rmin /
    b1b_static_rmax / b2_modulated / b3_context_aug / b4_full_loop) 가 잠금.
    runner.py 는 helper 출력 만 입력 받으므로 내부 일관성 보장됨.
    """

    mode: BaselineMode
    tier1_mode: str
    context_aug: bool
    tier2_enabled: bool

    def __post_init__(self) -> None:
        if not isinstance(self.mode, BaselineMode):
            raise TypeError(
                f"mode 는 BaselineMode 여야 함, got {type(self.mode).__name__}"
            )
        if self.tier1_mode not in _VALID_TIER1_MODES:
            raise ValueError(
                f"tier1_mode={self.tier1_mode!r} 무효 — "
                f"{_VALID_TIER1_MODES} 중 하나여야 함"
            )
        if not isinstance(self.context_aug, bool):
            raise TypeError(
                f"context_aug 는 bool 여야 함, got {type(self.context_aug).__name__}"
            )
        if not isinstance(self.tier2_enabled, bool):
            raise TypeError(
                f"tier2_enabled 는 bool 여야 함, "
                f"got {type(self.tier2_enabled).__name__}"
            )
        # ablation 정합성: tier2 게이트는 intent layer (context_aug) 위에서 동작.
        # context_aug=False + tier2_enabled=True 는 정의되지 않은 조합 (paper §C
        # 격자에 없음).
        if self.tier2_enabled and not self.context_aug:
            raise ValueError(
                f"tier2_enabled=True 면 context_aug=True 필요 — "
                f"paper §C 격자 정의 위반 (B4 만 tier2_enabled=True, "
                f"B4 는 context_aug=True)"
            )
