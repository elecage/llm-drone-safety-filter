"""ADR-0025 D2 — query rate (QR).

정의: $\\text{QR} = n_\\text{ask\\_user} / T$ — 단위 시간당 명료화 질문 빈도 [1/s].

paper §C 측 가설 = B2 < B1 (변조가 confirm 자동화). ARS (사용자 자율감)
측 paired metric — ARS 측 *명령 단위 비율*, QR 측 *시간 단위 빈도*.

$n_\\text{ask\\_user}$(parameter 명) 측 실제 산출 = 게이트 ``confirm`` 결정
count (`eval_metrics.bag_signals.count_decisions`, ADR-0032 amendment
2026-07-03 정정 — gate_node 는 리터럴 ``ask_user`` 를 발행하지 않는다).
"""

from __future__ import annotations


def query_rate(n_ask_user: int, episode_duration_s: float) -> float:
    """QR = $n_\\text{ask\\_user}$ / T [1/s].

    Args:
        n_ask_user: Tier 2 측 ``confirm`` 처분(=사용자 확인 요청) 횟수.
        episode_duration_s: episode 측 wall-clock duration [s].

    Returns:
        QR $\\geq 0$, 단위 [1/s].

    Raises:
        ValueError: n_ask_user 음수 또는 duration $\\leq 0$.
    """
    if n_ask_user < 0:
        raise ValueError(f'n_ask_user 음수 거부 — got {n_ask_user}')
    if episode_duration_s <= 0.0:
        raise ValueError(
            f'episode_duration_s 양의 실수 — got {episode_duration_s}'
        )
    return n_ask_user / episode_duration_s
