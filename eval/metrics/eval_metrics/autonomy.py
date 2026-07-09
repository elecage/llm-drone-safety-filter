"""ADR-0025 D2 — autonomy response score (ARS).

정의 (ADR-0025 D2 amendment 2):
$$
\\text{ARS} = 1 - \\frac{n_\\text{ask\\_user}}{n_\\text{commands}}
$$

분모 명세 ([ADR-0025 D2 amendment 2](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d2)):
$n_\\text{commands}$ 측 Tier 2 가 처분한 *전체 σ 수*(confirm 포함) — 정합
분수. 경계 경우 $n_\\text{commands} = 0$ (episode 안 σ 도착 X) → ARS $:= 1$
잠금 — paper §C 측 결측 NaN 회피, "개입 0" 측 의미 정합.

$n_\\text{ask\\_user}$(parameter 명) 측 실제 산출 = 게이트 ``confirm`` 결정
count (`eval_metrics.bag_signals.count_decisions`, ADR-0032 amendment
2026-07-03 정정 — gate_node 는 리터럴 ``ask_user`` 를 발행하지 않는다).

RESEARCH_CONTEXT §B1 측 *user-driven* 정합 — 사용자 자율감 proxy.
"""

from __future__ import annotations


def autonomy_response_score(n_ask_user: int, n_commands: int) -> float:
    """ARS = 1 - $n_\\text{ask\\_user}$ / $n_\\text{commands}$.

    Args:
        n_ask_user: Tier 2 측 ``confirm`` 처분(=사용자 확인 요청) 횟수.
        n_commands: Tier 2 처분 σ 전체 (confirm 포함).

    Returns:
        $\\text{ARS} \\in [0, 1]$. $n_\\text{commands} = 0$ 측 1 잠금.

    Raises:
        ValueError: n 음수 또는 $n_\\text{ask\\_user} > n_\\text{commands}$ (정합 위반).
    """
    if n_ask_user < 0:
        raise ValueError(f'n_ask_user 음수 거부 — got {n_ask_user}')
    if n_commands < 0:
        raise ValueError(f'n_commands 음수 거부 — got {n_commands}')
    if n_ask_user > n_commands:
        raise ValueError(
            f'n_ask_user > n_commands 정합 위반 — '
            f'n_ask_user(confirm)={n_ask_user}, commands={n_commands} '
            f'(ADR-0025 D2 amendment 2 분모 = Tier 2 전체 σ, confirm 포함)'
        )

    if n_commands == 0:
        return 1.0  # 경계 잠금 — "개입 0" 측 의미 정합
    return 1.0 - (n_ask_user / n_commands)
