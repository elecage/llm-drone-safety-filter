"""B3 context-augmented baseline — *변조 + context augmentation* (Tier 2 게이트 불활성).

ADR-0025 D3 격자 정의:

    B3 = tier1_mode 'b2' + context_aug True + tier2_enabled False

즉 B3 trial 의 데이터 흐름:

    [사용자 발화 prompt]
       → [intent/llm/ wrapper — context graph + ego-stream 융합 적용 LLM]
       → /intent/typed_action (TypedAction σ)
       → [intent/confidence/estimator_node — c 추정]
       → /intent/grounding_confidence (Float32, c ∈ [0,1])
       → [tier1_filter mode='b2' — 신뢰도 변조 r(c̃) CBF-QP]
       → /cmd/trajectory_setpoint_safe / pose_setpoint_safe
       → [G1: ENU→NED 변환 + PX4 packing]

context augmentation = paper §6 fusion — *고정 context graph* (사용자 위치 ·
가구·known objects) + *드론 자기중심 stream* (OVD detection · ego-pose) 을 LLM
prompt 에 함께 주입 → LLM 의 referential grounding 능력 향상 (지시 대상 결정
정확도 ↑). 본 baseline 의 가설 = context_aug 가 σ 의 grounding error 를 줄여
$\\tilde c$ 를 *체계적으로 더 높게* 유지 → $r(\\tilde c) \\to r_\\text{min}$ 근접
→ 과보수성 $\\bar r$ 개선 + 안전 마진 유지.

Tier 2 게이트 (시간논리 사양 $\\Phi_1$/$\\Phi_8$/$\\Phi_9$/$\\Phi_{10}$ 등) 는
*불활성* — LLM 출력 σ 가 직접 tier1 으로 전달. B4 와 비교 시 *tier2_enabled
단독 차이* → Tier 2 게이트 효과 측정.

본 baseline 은 ablation 의 *context fusion 효과 측정 자리* — B2 (변조 단독)
대비 B3 차이가 *context augmentation 의 grounding error 감소 효과* 측정. ADR-0025
D2 의 메트릭 6 종 모두 측정 → paper §C 표 1 넷째 행. 특히 query rate (QR)
및 과보수성 $\\bar r$ 측 개선 가설 검증 자리.

scope: 본 PR (B7 #10) 의 launch 는 tier1_filter mode='b2' 단독 stub. 실 intent
layer 노드 (intent/llm/ wrapper + intent/confidence/estimator_node + context
graph publisher) 의 launch 합성은 B7 #12 runner.py 측 *intent/llm wrapper 선택*
입력에 따라 결정 — 본 모듈은 BaselineConfig contract 만 잠금.
"""

from __future__ import annotations

from eval_baselines.schemas import BaselineConfig, BaselineMode


def b3_config() -> BaselineConfig:
    """B3 context-augmented baseline 의 BaselineConfig 잠금.

    Returns
    -------
    BaselineConfig
        mode=B3, tier1_mode='b2', context_aug=True, tier2_enabled=False.
        runner.py 가 본 config 의 context_aug=True 를 보고 intent/llm/ wrapper
        측 context fusion 모드를 선택 + intent/confidence/estimator_node 와 함께
        launch 합성.
    """
    return BaselineConfig(
        mode=BaselineMode.B3,
        tier1_mode='b2',
        context_aug=True,
        tier2_enabled=False,
    )
