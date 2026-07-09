"""B1b static baseline — *정적 $r_\\text{max}$ CBF-QP 안전 필터* (intent layer 불활성).

ADR-0025 D3 격자 정의 (amendment 19 — B1→B1a/B1b 분리):

    B1b = tier1_mode 'b1_max' + context_aug False + tier2_enabled False

즉 B1b trial 의 데이터 흐름:

    [nominal source: G2 player / 정상 LLM 출력]
       → /cmd/trajectory_setpoint_nominal
       → /cmd/pose_setpoint_nominal
       → [tier1_filter mode='b1_max' — 정적 r_max CBF-QP]
       → /cmd/trajectory_setpoint_safe
       → /cmd/pose_setpoint_safe
       → [G1: ENU→NED 변환 + PX4 packing]

cmsm-proof §5 명제 1 정합 — $r = r_\\text{max}$ 고정 $\\Rightarrow$ 안전 집합
$\\{x : \\lVert p - p_\\text{user}\\rVert \\geq r_\\text{max}\\}$ 전방불변성 보장. B1a 와 동일
정적 CBF 로직이되 반경만 $r_\\text{max}$. 신뢰도 입력 없음.

tier1_filter 가 *실제 $r_\\text{max}$ 로 비행* — 메트릭 상수($r_\\text{max}$)만이 아니라
작업 성공률(SR)·과보수성($\\bar r$)을 *실 비행* 으로 측정하기 위함 (ADR-0025
amendment 19). B1a 대비 더 보수적 → 과보수성 상승 + 안전 위반 동일(0).

intent layer (context augmentation · 신뢰도 추정 · Tier 2 게이트) 모두 *불활성*.

본 baseline 은 ADR-0025 amendment 19 의 C2 트레이드오프에서 *안전점* — B2(변조)가
B1a(효율점)·B1b(안전점)를 모두 dominate 함을 입증하는 대조군. ADR-0025 D2 의 메트릭
6 종 모두 측정 → paper §C 표 1.
"""

from __future__ import annotations

from eval_baselines.schemas import BaselineConfig, BaselineMode


def b1b_config() -> BaselineConfig:
    """B1b static (정적 $r_\\text{max}$) baseline 의 BaselineConfig 잠금.

    Returns
    -------
    BaselineConfig
        mode=B1B, tier1_mode='b1_max', context_aug=False, tier2_enabled=False.
        runner.py 가 본 config 를 입력 받아 launch 구성을 결정.
    """
    return BaselineConfig(
        mode=BaselineMode.B1B,
        tier1_mode='b1_max',
        context_aug=False,
        tier2_enabled=False,
    )
