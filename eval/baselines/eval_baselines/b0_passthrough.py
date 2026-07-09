"""B0 passthrough baseline — *안전 필터 없음* (paper §C 5-way ablation 최하단).

ADR-0025 D3 격자 정의:

    B0 = tier1_mode 'b0' + context_aug False + tier2_enabled False

즉 B0 trial 의 데이터 흐름:

    [nominal source: G2 player / 정상 LLM 출력]
       → /cmd/trajectory_setpoint_nominal
       → /cmd/pose_setpoint_nominal
       → [tier1_filter mode='b0' — pass-through]
       → /cmd/trajectory_setpoint_safe
       → /cmd/pose_setpoint_safe
       → [G1: ENU→NED 변환 + PX4 packing]

intent layer (context augmentation · 신뢰도 추정 · Tier 2 게이트) 모두 *불활성*.
LLM 명령이 *어떤 σ 든* 그대로 PX4 까지 전달 → paper §C 표 1 의 *안전 위반 lower
bound* (V > 0 fault 변형에서 충돌 발생 입증).

본 baseline 은 ablation 의 *대조군* — B1/B2/B3/B4 의 단계별 효과 측정을 위한
zero 기준. ADR-0025 D2 의 메트릭 (V · SR · ARS · QR · $\\bar r$ · $\\tau_\\text{loop}$)
모두 본 baseline 측 측정 → paper §C 표 1 첫 행.
"""

from __future__ import annotations

from eval_baselines.schemas import BaselineConfig, BaselineMode


def b0_config() -> BaselineConfig:
    """B0 passthrough baseline 의 BaselineConfig 잠금.

    Returns
    -------
    BaselineConfig
        mode=B0, tier1_mode='b0', context_aug=False, tier2_enabled=False.
        runner.py 가 본 config 를 입력 받아 launch 구성을 결정.
    """
    return BaselineConfig(
        mode=BaselineMode.B0,
        tier1_mode='b0',
        context_aug=False,
        tier2_enabled=False,
    )
