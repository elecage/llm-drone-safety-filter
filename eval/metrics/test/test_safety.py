"""eval_metrics.safety 단위 테스트 — V = 1[h<0] 시간 비율."""

from __future__ import annotations

import pytest

from eval_metrics.safety import safety_violation_rate
from eval_metrics.schemas import TimeSeries


class TestSafetyViolationRate:
    def test_no_violation_all_positive(self):
        """h > 0 항상 → V = 0."""
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0, 3.0),
            values=(0.5, 0.6, 0.4, 0.3),
        )
        assert safety_violation_rate(ts) == 0.0

    def test_full_violation_all_negative(self):
        """h < 0 항상 → V = 1 (마지막 sample 제외 — left-rect)."""
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0, 3.0),
            values=(-0.1, -0.2, -0.3, -0.4),
        )
        # left-rect: 마지막 sample (t=3.0) 측 indicator 사용 안 함 →
        # 0..1, 1..2, 2..3 = 3 segment 측 모두 violation → 3/3 = 1.0
        assert safety_violation_rate(ts) == 1.0

    def test_partial_violation_half(self):
        """첫 절반 violation, 둘째 절반 safe — V = 0.5.

        left-rect 측 segment $[t_i, t_{i+1}]$ 측 *시작 sample* indicator 사용.
        $h = (-0.1, 0.5, 0.6)$ 측 segment 측 [0,1] (h_0=-0.1 violation) +
        [1,2] (h_1=0.5 safe) → V = 1/2 = 0.5.
        """
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0),
            values=(-0.1, 0.5, 0.6),  # 0..1 violation only
        )
        assert safety_violation_rate(ts) == 0.5

    def test_last_sample_ignored_by_left_rect(self):
        """PR #108 review D-6 명시 — 마지막 sample indicator 무시 검증.

        $h = (0.5, 0.5, -0.1)$ 측 마지막 sample $h = -0.1$ 측 segment 측
        right-boundary 라 indicator 사용 안 됨 → V = 0.0.
        """
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0),
            values=(0.5, 0.5, -0.1),
        )
        assert safety_violation_rate(ts) == 0.0

    def test_boundary_h_zero_no_violation(self):
        """h == 0 측 *경계* — invariant 측 $h \\geq 0$ → not violation."""
        ts = TimeSeries(
            timestamps=(0.0, 1.0, 2.0),
            values=(0.0, 0.0, 0.0),
        )
        assert safety_violation_rate(ts) == 0.0

    def test_single_sample_rejected(self):
        with pytest.raises(ValueError, match=r'sample.*2'):
            safety_violation_rate(TimeSeries(timestamps=(0.0,), values=(0.5,)))

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match=r'sample.*2'):
            safety_violation_rate(TimeSeries(timestamps=(), values=()))

    def test_zero_duration_rejected(self):
        with pytest.raises(ValueError, match='duration'):
            safety_violation_rate(
                TimeSeries(timestamps=(1.0, 1.0), values=(0.5, 0.5)),
            )
