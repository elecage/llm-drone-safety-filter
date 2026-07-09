"""B1a static baseline — *정적 $r_\\text{min}$ CBF-QP 안전 필터* (intent layer 불활성).

ADR-0025 D3 격자 정의 (amendment 19 — B1→B1a/B1b 분리):

    B1a = tier1_mode 'b1' + context_aug False + tier2_enabled False

즉 B1a trial 의 데이터 흐름:

    [nominal source: G2 player / 정상 LLM 출력]
       → /cmd/trajectory_setpoint_nominal
       → /cmd/pose_setpoint_nominal
       → [tier1_filter mode='b1' — 정적 r_min CBF-QP]
       → /cmd/trajectory_setpoint_safe
       → /cmd/pose_setpoint_safe
       → [G1: ENU→NED 변환 + PX4 packing]

cmsm-proof §5 명제 1 정합 — $r = r_\\text{min}$ 고정 $\\Rightarrow$ 안전 집합
$\\mathcal{C}_\\text{floor} = \\{x : \\lVert p - p_\\text{user}\\rVert \\geq r_\\text{min}\\}$
전방불변성 보장. 신뢰도 입력 없음 (B2 와 비교 가능한 fixed-margin baseline).

intent layer (context augmentation · 신뢰도 추정 · Tier 2 게이트) 모두 *불활성*.
LLM 명령이 *어떤 σ 든* tier1 의 정적 마진 안에서만 통과 → paper §C 표 1 의
*고정 마진 효율점* (최소 마진, B1b 정적 $r_\\text{max}$ 안전점과 대비).

본 baseline 은 ADR-0025 amendment 19 의 C2 트레이드오프에서 *효율점* — B2(변조)가
B1a(효율)·B1b(안전)를 모두 dominate 함을 입증하는 대조군. ADR-0025 D2 의 메트릭
6 종 모두 측정 → paper §C 표 1.
"""

from __future__ import annotations

from eval_baselines.schemas import BaselineConfig, BaselineMode


def b1a_config() -> BaselineConfig:
    """B1a static (정적 $r_\\text{min}$) baseline 의 BaselineConfig 잠금.

    Returns
    -------
    BaselineConfig
        mode=B1A, tier1_mode='b1', context_aug=False, tier2_enabled=False.
        runner.py 가 본 config 를 입력 받아 launch 구성을 결정.
    """
    return BaselineConfig(
        mode=BaselineMode.B1A,
        tier1_mode='b1',
        context_aug=False,
        tier2_enabled=False,
    )
