"""ADR-0025 D2 — safety violation rate $V$.

정의 (ADR-0025 D2 amendment 2026-05-26):
$$
V = \\frac{1}{T} \\int_0^T \\mathbb{1}[h(x(t)) < 0] \\, dt
$$

$h(x(t)) = \\lVert p_\\text{drone}(t) - p_\\text{user} \\rVert - r(\\tilde c(t))$ 측 시계열
입력. ``h < 0`` 측 사용자 회피 영역 *내부* 침입 측 시간 비율.

cmsm-proof §6 정리 2 가 *형식상* $V = 0$ 보장 (tier1 active 시). paper §C 측
B0 (필터 없음) > 0, B1/B2 (필터 active) = 0 측 *기대 결과*. ADR-0025 D6 측
*binary* 잠금 (\\int \\max(0, -h) dt 측 누적 침입 깊이는 ROADMAP C15 backlog).
"""

from __future__ import annotations

from eval_metrics.schemas import TimeSeries


def safety_violation_rate(h_series: TimeSeries) -> float:
    """V = $\\frac{1}{T} \\int_0^T \\mathbb{1}[h < 0] dt$.

    적분 측 *left-rectangle* (piecewise constant indicator). 두 sample 측
    segment $[t_i, t_{i+1}]$ 측 시작 sample 측 indicator $\\mathbb{1}[h_i < 0]$
    측 사용 — segment 측 *right-boundary* sample 측 indicator 측 *사용 안 함*.
    *마지막 sample* (segment 부재) 측 indicator 측 무시.

    예 (PR #108 review D-6 명시):
      - $h = (0.5, -0.1, -0.1)$ 측 $V = 0.5$ (마지막 segment 만 violation;
        시작 sample 측 violation 0 측 첫 segment 측 sub).
      - $h = (0.5, 0.5, -0.1)$ 측 $V = 0.0$ (마지막 sample $h = -0.1$ 측
        indicator 사용 안 됨 — segment 측 right-boundary).

    h 측 sign change 측 sample resolution 한계 — bag_reader 측 충분 fine
    sample (paper §C 측 tier1 50 ms loop 측 20 Hz sample) 권장.

    Args:
        h_series: TimeSeries — timestamps [s] + h(x(t)) 측 values.

    Returns:
        $V \\in [0, 1]$.

    Raises:
        ValueError: 시계열 측 sample 측 $< 2$ — 적분 정의 안 됨.
    """
    n = len(h_series.timestamps)
    if n < 2:
        raise ValueError(
            f'safety_violation_rate 측 sample $\\geq 2$ 필요 — got n={n}'
        )

    t0, t_end = h_series.timestamps[0], h_series.timestamps[-1]
    total_duration = t_end - t0
    if total_duration <= 0.0:
        raise ValueError(
            f'시계열 측 duration $> 0$ 필요 — got t0={t0}, t_end={t_end}'
        )

    violation_duration = 0.0
    for i in range(n - 1):
        dt = h_series.timestamps[i + 1] - h_series.timestamps[i]
        if h_series.values[i] < 0.0:
            violation_duration += dt

    return violation_duration / total_duration
