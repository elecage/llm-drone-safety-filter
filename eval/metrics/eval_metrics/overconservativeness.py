"""ADR-0025 D2 amendment 3 — overconservativeness $\\bar r$.

정의:
$$
\\bar r = \\frac{1}{T} \\int_0^T r(\\tilde c(t)) \\, dt
$$

회피 영역 반경의 *시간 평균* [m]. 안전 보장 (tier1 active, $V=0$) 하에서
*덜 보수* 가 더 *유용한 작업* — cmsm-proof §8 L1 낙관성 측 정량.

기준 ([ADR-0025 D2 amendment 3](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d2)):
- $r_\\text{min}$ 에 가까울수록 효율적
- $r_\\text{max}$ 에 가까울수록 보수적
- B0 측 *r 없음* (정의 안 됨 — V 는 worst-case $r_\\text{max}$ 영역으로 측정)
- B1a 측 const = $r_\\text{min}$ (효율 baseline, tier1 `b1`)
- B1b 측 const = $r_\\text{max}$ (안전 baseline, tier1 `b1_max`)
- B2 측 $r_\\text{min} \\leq \\bar r$(B2) $\\leq r_\\text{max}$ (변조). **C2 = B2 가 B1a
  (효율점)·B1b(안전점)를 모두 dominate** — 명확→B1a 수준 효율, 모호→B1b 수준 안전
  (ADR-0025 amendment 19; 세션 48 중간의 "B1=r_min" 주석은 B1a/B1b 분리로 대체).

D6 측 *누적 침입 깊이* (위반 심각도) 와 *혼동 금지* — 본 metric 측 *안전 영역
내부 측 비효율* 측정, D6 측 *위반 발생 시 심각도* 측정.
"""

from __future__ import annotations

from eval_metrics.schemas import TimeSeries


def overconservativeness(r_series: TimeSeries) -> float:
    """$\\bar r = \\frac{1}{T} \\int_0^T r(\\tilde c(t)) dt$ [m].

    적분 측 trapezoidal rule — piecewise linear 가정 (sample 사이 선형 보간).

    Args:
        r_series: TimeSeries — timestamps [s] + $r(\\tilde c(t))$ 측 values [m].

    Returns:
        $\\bar r \\geq r_\\text{min}$ ($r_\\text{min} > 0$ 이므로 양의 실수).

    Note (PR #108 review D-7):
        ``r = 0`` 측 *허용* — runtime 측 boundary case 측 graceful pass.
        ADR-0026 D4 측 ``r_min > 0`` 측 *시뮬 설계* invariant 보장 — runtime
        측 일시적 ``r = 0`` (예: tier1 active 전 boot phase) 측 metric 측
        graceful 측정 가능. 음수 r 만 거부 — 물리적 invariant 위반.

    Raises:
        ValueError: 시계열 측 sample $< 2$ 또는 duration $\\leq 0$ 또는 음의 r.
    """
    n = len(r_series.timestamps)
    if n < 2:
        raise ValueError(
            f'overconservativeness 측 sample $\\geq 2$ 필요 — got n={n}'
        )

    t0, t_end = r_series.timestamps[0], r_series.timestamps[-1]
    duration = t_end - t0
    if duration <= 0.0:
        raise ValueError(
            f'시계열 측 duration $> 0$ 필요 — got t0={t0}, t_end={t_end}'
        )

    # negative r 거부 — 물리적 invariant ($r > 0$, ADR-0026 D4)
    for i, r in enumerate(r_series.values):
        if r < 0.0:
            raise ValueError(
                f'r_series.values[{i}]={r} 음수 거부 (회피 영역 반경 > 0)'
            )

    # trapezoidal rule
    integral = 0.0
    for i in range(n - 1):
        dt = r_series.timestamps[i + 1] - r_series.timestamps[i]
        integral += 0.5 * (r_series.values[i] + r_series.values[i + 1]) * dt

    return integral / duration
