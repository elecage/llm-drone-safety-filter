"""B4 full-loop baseline — *변조 + context + Tier 2 시간논리 사양* (전체 아키텍처).

ADR-0025 D3 격자 정의:

    B4 = tier1_mode 'b2' + context_aug True + tier2_enabled True

*PLANNED architecture* (B7 #12 runner.py + 후속 intent/llm wrapper PR 진입 시 실
합성):

    [사용자 발화 prompt]
       → [intent/llm/ wrapper — context graph + ego-stream 융합 적용 LLM]
       → /intent/typed_action_raw (TypedAction σ_raw)
       → [Tier 2 런타임 검증 게이트 — 시간논리 사양 $\\Phi_1$/$\\Phi_8$/$\\Phi_9$/$\\Phi_{10}$ 강제]
       → /intent/typed_action_gated (σ_gated, 또는 ask_user/return_to_dock 후퇴)
       → [intent/confidence/estimator_node — c 추정]
       → /intent/grounding_confidence (Float32, c ∈ [0,1])
       → [tier1_filter mode='b2' — 신뢰도 변조 r(c̃) CBF-QP]
       → /cmd/trajectory_setpoint_safe / pose_setpoint_safe
       → [G1: ENU→NED 변환 + PX4 packing]

Tier 2 런타임 검증 게이트 = 시간논리 사양 (Temporal Spec) — LLM 출력 σ_raw 가
*계획 수준* 안전 사양 (Φ_1 geofence + Φ_2 battery + Φ_3 confirm 강제 + Φ_8
자기수정 빈도 + Φ_9 응답 timeout + Φ_10 명령 모순 등) 검증을 통과해야만 tier1
으로 전달. 위반 시 ask_user 명료화 또는 return_to_dock 후퇴. ADR-0017 (인지 단절
시간논리 사양 배치) + ADR-0025 D1 (Tier 2 cover 표) 정합.

본 baseline 의 가설 = Tier 2 게이트가 *계획 수준* 결함 (좌표가 geofence 밖,
LLM σ 측 자기모순 등) 을 *조기 차단* 해 tier1 부담 ↓ + ARS (autonomy-respect
score) 동일 또는 ↑ + V 추가 감소 (tier1 만으로 보장 안 되는 *계획 수준* 위반
케이스 cover).

본 baseline 은 ablation 의 *전체 아키텍처* 자리 — B3 (변조+context, Tier 2 없음)
대비 B4 차이가 *Tier 2 시간논리 사양 게이트 효과* 측정. paper §C 표 1 다섯째 행
+ paper-1 의 C3 기여 (계획 수준 런타임 검증 게이트, 타입드 스킬 API + 시간논리,
ms 오버헤드) 직접 입증 자리.

scope: 본 PR (B7 #11) 의 launch 는 tier1_filter mode='b2' 단독 stub (B0/B1/B2/B3
패턴 정합). 실 intent layer 노드 (intent/llm/ wrapper + Tier 2 게이트 노드 +
intent/confidence/estimator_node) 의 launch 합성은 B7 #12 runner.py 측 BaselineConfig
입력 → wrapper 선택 logic 에서 결정 — 본 모듈은 BaselineConfig contract 만 잠금.
"""

from __future__ import annotations

from eval_baselines.schemas import BaselineConfig, BaselineMode


def b4_config() -> BaselineConfig:
    """B4 full-loop baseline 의 BaselineConfig 잠금.

    Returns
    -------
    BaselineConfig
        mode=B4, tier1_mode='b2', context_aug=True, tier2_enabled=True.
        runner.py 가 본 config 의 tier2_enabled=True 를 보고 Tier 2 게이트 노드 +
        context fusion intent/llm wrapper + intent/confidence/estimator_node 와
        함께 launch 합성.
    """
    return BaselineConfig(
        mode=BaselineMode.B4,
        tier1_mode='b2',
        context_aug=True,
        tier2_enabled=True,
    )
