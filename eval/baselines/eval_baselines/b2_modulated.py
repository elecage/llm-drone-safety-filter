"""B2 modulated baseline — *신뢰도 변조 CBF-QP 안전 필터* (intent layer 불활성).

ADR-0025 D3 격자 정의:

    B2 = tier1_mode 'b2' + context_aug False + tier2_enabled False

즉 B2 trial 의 데이터 흐름:

    [nominal source: G2 player / 정상 LLM 출력]
       → /cmd/trajectory_setpoint_nominal
       → /cmd/pose_setpoint_nominal
       → [tier1_filter mode='b2' — 신뢰도 변조 r(c̃) CBF-QP]
       → /cmd/trajectory_setpoint_safe
       → /cmd/pose_setpoint_safe
       → [G1: ENU→NED 변환 + PX4 packing]

cmsm-proof §6 정리 2 정합 — $r(c) = r_\\text{min} + (1-c)(r_\\text{max}-r_\\text{min})$
+ 변화율 제한기 (dot_c_max = u_max/(r_max-r_min)) 하 시변 $\\tilde c(t)$ 안전집합
전방불변성 보장. 신뢰도 입력 토픽 `/intent/grounding_confidence` 미수신 시
fail-active default $\\tilde c = 1.0$ → $r = r_\\text{min}$ → B1 과 동일 거동.

intent layer (context augmentation · 신뢰도 추정 노드) 측 *context_aug* 불활성 —
즉 신뢰도 c 는 *외부 source* 에서 publish (paper §C 측 fault injector 또는
intent/confidence/estimator_node 의 외부 입력) → tier1_filter 가 직접 변조 적용.
Tier 2 게이트도 불활성.

본 baseline 은 ablation 의 *변조 효과 측정 자리* — B1 (정적 r_min) 대비 B2 차이가
*신뢰도 변조의 안전 마진 vs 과보수성 trade-off* 핵심 기여 (paper-1 C2). ADR-0025
D2 의 메트릭 6 종 모두 측정 → paper §C 표 1 셋째 행.
"""

from __future__ import annotations

from eval_baselines.schemas import BaselineConfig, BaselineMode


def b2_config() -> BaselineConfig:
    """B2 modulated baseline 의 BaselineConfig 잠금.

    Returns
    -------
    BaselineConfig
        mode=B2, tier1_mode='b2', context_aug=False, tier2_enabled=False.
        runner.py 가 본 config 를 입력 받아 launch 구성을 결정.
    """
    return BaselineConfig(
        mode=BaselineMode.B2,
        tier1_mode='b2',
        context_aug=False,
        tier2_enabled=False,
    )
