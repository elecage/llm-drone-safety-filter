"""eval_metrics.overconservativeness 단위 테스트 — \\bar r = ∫r dt / T."""

from __future__ import annotations

import pytest

from eval_metrics.overconservativeness import overconservativeness
from eval_metrics.schemas import TimeSeries


class TestOverconservativeness:
    def test_constant_r(self):
        """r = const → \\bar r = const."""
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0, 3.0),
            values=(1.0, 1.0, 1.0, 1.0),
        )
        assert overconservativeness(ts) == 1.0

    def test_linear_ramp(self):
        """r linear ramp 0 → 2 over [0, 2] → trapezoidal mean = 1.0."""
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0),
            values=(0.0, 1.0, 2.0),
        )
        # trapezoidal: (0+1)/2 * 1 + (1+2)/2 * 1 = 0.5 + 1.5 = 2.0
        # /2.0 duration = 1.0
        assert abs(overconservativeness(ts) - 1.0) < 1e-9

    def test_two_sample(self):
        """최소 sample 2 — (r0 + r1) / 2 (trapezoidal exact)."""
        ts = TimeSeries(timestamps=(0.0, 1.0), values=(0.7, 1.3))
        assert abs(overconservativeness(ts) - 1.0) < 1e-9  # 0.5 * (0.7+1.3) = 1.0

    def test_r_min_lower_bound_typical(self):
        """B2 가설: r_min ≤ \\bar r ≤ r_max — r 측 0.9 (r_min) ~ 1.5 (r_max)."""
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0, 3.0),
            values=(0.9, 1.0, 1.2, 1.5),
        )
        bar_r = overconservativeness(ts)
        assert 0.9 <= bar_r <= 1.5

    def test_negative_r_rejected(self):
        with pytest.raises(ValueError, match='음수'):
            overconservativeness(
                TimeSeries(timestamps=(0.0, 1.0), values=(1.0, -0.1)),
            )

    def test_single_sample_rejected(self):
        with pytest.raises(ValueError, match=r'sample.*2'):
            overconservativeness(TimeSeries(timestamps=(0.0,), values=(1.0,)))

    def test_zero_duration_rejected(self):
        with pytest.raises(ValueError, match='duration'):
            overconservativeness(
                TimeSeries(timestamps=(1.0, 1.0), values=(1.0, 1.0)),
            )
