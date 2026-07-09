"""eval_metrics.latency 단위 테스트 — \\tau_loop = max(periods) [s]."""

from __future__ import annotations

import pytest

from eval_metrics.latency import realtime_latency


class TestRealtimeLatency:
    def test_typical_periods(self):
        """tier1 측 ~ 50 ms loop — max = 0.052 s."""
        periods = [0.050, 0.051, 0.052, 0.049, 0.050]
        assert realtime_latency(periods) == 0.052

    def test_single_period(self):
        assert realtime_latency([0.050]) == 0.050

    def test_passes_50ms_threshold(self):
        """tier1 결정론 보장 = 50 ms — 모든 period < 50 ms."""
        periods = [0.045, 0.048, 0.049, 0.047]
        tau = realtime_latency(periods)
        assert tau < 0.050  # paper §C B7 검증

    def test_violates_50ms_threshold(self):
        """50 ms 초과 — paper §C 측 baseline 결정론 위반."""
        periods = [0.045, 0.060, 0.048]  # 60 ms 초과
        tau = realtime_latency(periods)
        assert tau > 0.050

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match='빈 list'):
            realtime_latency([])

    def test_negative_period_rejected(self):
        with pytest.raises(ValueError, match='음수'):
            realtime_latency([0.050, -0.001, 0.052])
