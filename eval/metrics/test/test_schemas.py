"""eval_metrics.schemas 단위 테스트 — TimeSeries + TrialMetadata invariant."""

from __future__ import annotations

import pytest

from eval_metrics.schemas import TimeSeries, TrialMetadata


# ----------------------------------------------------------- TimeSeries


class TestTimeSeries:
    def test_valid_monotonic(self):
        ts = TimeSeries(
            timestamps=(0.0, 0.1, 0.2, 0.3),
            values=(1.0, 2.0, 3.0, 4.0),
        )
        assert len(ts.timestamps) == 4

    def test_valid_empty(self):
        """빈 시계열 측 OK (metric 측 별도 처리)."""
        ts = TimeSeries(timestamps=(), values=())
        assert len(ts.timestamps) == 0

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match='동일 길이'):
            TimeSeries(timestamps=(0.0, 0.1), values=(1.0, 2.0, 3.0))

    def test_non_monotonic_clamped(self):
        # 역행 timestamp 는 clamp(레코더 jitter 흡수), raise 안 함 (2026-07-01 적발 버그).
        ts = TimeSeries(
            timestamps=(0.0, 0.2, 0.1),  # 0.1 < 0.2 → clamp to 0.2
            values=(1.0, 2.0, 3.0),
        )
        assert ts.timestamps == (0.0, 0.2, 0.2)
        assert all(ts.timestamps[i] >= ts.timestamps[i - 1]
                   for i in range(1, len(ts.timestamps)))

    def test_equal_consecutive_ok(self):
        """단조 *비*감소 — 동일 timestamp 측 OK (sample resolution 한계 측)."""
        ts = TimeSeries(
            timestamps=(0.0, 0.1, 0.1, 0.2),
            values=(1.0, 2.0, 2.5, 3.0),
        )
        assert ts.timestamps[1] == ts.timestamps[2]


# ----------------------------------------------------------- TrialMetadata


class TestTrialMetadata:
    def test_valid_none_trial(self):
        meta = TrialMetadata(
            scenario='S5', baseline='B0', fault_class='none',
            fault_variant='', seed=42, wall_clock_s=60.0,
        )
        assert meta.fault_class == 'none'

    def test_valid_hallucination_trial(self):
        meta = TrialMetadata(
            scenario='S6', baseline='B2', fault_class='hallucination',
            fault_variant='position_noise_gauss_low',
            seed=42, wall_clock_s=72.5,
        )
        assert meta.fault_variant == 'position_noise_gauss_low'

    def test_invalid_scenario_rejected(self):
        with pytest.raises(ValueError, match='scenario'):
            TrialMetadata(
                scenario='S99', baseline='B0', fault_class='none',
                fault_variant='', seed=42, wall_clock_s=60.0,
            )

    def test_invalid_baseline_rejected(self):
        with pytest.raises(ValueError, match='baseline'):
            TrialMetadata(
                scenario='S5', baseline='B99', fault_class='none',
                fault_variant='', seed=42, wall_clock_s=60.0,
            )

    def test_invalid_fault_class_rejected(self):
        with pytest.raises(ValueError, match='fault_class'):
            TrialMetadata(
                scenario='S5', baseline='B0', fault_class='unknown',
                fault_variant='', seed=42, wall_clock_s=60.0,
            )

    def test_none_with_variant_rejected(self):
        with pytest.raises(ValueError, match='fault_class=none'):
            TrialMetadata(
                scenario='S5', baseline='B0', fault_class='none',
                fault_variant='unexpected', seed=42, wall_clock_s=60.0,
            )

    def test_non_none_empty_variant_rejected(self):
        with pytest.raises(ValueError, match='fault_variant 필수'):
            TrialMetadata(
                scenario='S5', baseline='B0', fault_class='hallucination',
                fault_variant='', seed=42, wall_clock_s=60.0,
            )

    def test_zero_wall_clock_rejected(self):
        with pytest.raises(ValueError, match='wall_clock_s'):
            TrialMetadata(
                scenario='S5', baseline='B0', fault_class='none',
                fault_variant='', seed=42, wall_clock_s=0.0,
            )

    def test_bag_status_defaults_unknown(self):
        """bag_status default 'unknown' — legacy meta 호환 (silent 'complete' 금지)."""
        meta = TrialMetadata(
            scenario='S5', baseline='B0', fault_class='none',
            fault_variant='', seed=42, wall_clock_s=60.0,
        )
        assert meta.bag_status == 'unknown'

    def test_bag_status_allowed_values(self):
        for status in ('complete', 'incomplete', 'unknown'):
            meta = TrialMetadata(
                scenario='S5', baseline='B0', fault_class='none',
                fault_variant='', seed=42, wall_clock_s=60.0,
                bag_status=status,
            )
            assert meta.bag_status == status

    def test_invalid_bag_status_rejected(self):
        with pytest.raises(ValueError, match='bag_status'):
            TrialMetadata(
                scenario='S5', baseline='B0', fault_class='none',
                fault_variant='', seed=42, wall_clock_s=60.0,
                bag_status='maybe',
            )
