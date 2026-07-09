"""eval_metrics.bag_signals 단위 테스트 — 6 토픽 → metric input helpers."""

from __future__ import annotations

import json
import math

import pytest

from eval_metrics.bag_signals import (
    count_decisions,
    gate_rejection_rate,
    extract_loop_periods,
    extract_r_from_estimator_reports,
    positions_to_h_series,
)
from eval_metrics.schemas import TimeSeries


# ----------------------------------------------------------- positions_to_h_series


class TestPositionsToHSeries:
    def test_constant_r_constant_drone(self):
        """드론 측 user 측 1.0 m 거리 고정 + r = 0.7 → h = 0.3."""
        r_series = TimeSeries(
            timestamps=(0.0, 1.0, 2.0),
            values=(0.7, 0.7, 0.7),
        )
        positions = [
            (0.0, (1.0, 0.0, 0.0)),
            (1.0, (1.0, 0.0, 0.0)),
            (2.0, (1.0, 0.0, 0.0)),
        ]
        h = positions_to_h_series(positions, (0.0, 0.0, 0.0), r_series)
        assert h.timestamps == (0.0, 1.0, 2.0)
        for v in h.values:
            assert abs(v - 0.3) < 1e-9

    def test_drone_approaching_user_h_decreases(self):
        """드론 측 user 측 1.5 → 0.5 거리 접근 + r = 0.7 → h: 0.8 → -0.2."""
        r_series = TimeSeries(timestamps=(0.0, 2.0), values=(0.7, 0.7))
        positions = [
            (0.0, (1.5, 0.0, 0.0)),
            (1.0, (1.0, 0.0, 0.0)),
            (2.0, (0.5, 0.0, 0.0)),
        ]
        h = positions_to_h_series(positions, (0.0, 0.0, 0.0), r_series)
        assert abs(h.values[0] - 0.8) < 1e-9
        assert abs(h.values[1] - 0.3) < 1e-9
        assert abs(h.values[2] - (-0.2)) < 1e-9

    def test_3d_distance(self):
        """3D euclidean — (1, 2, 2) 측 origin 측 ||·|| = 3.0 + r=0.7 → h=2.3."""
        r_series = TimeSeries(timestamps=(0.0,), values=(0.7,))
        positions = [(0.0, (1.0, 2.0, 2.0))]
        h = positions_to_h_series(positions, (0.0, 0.0, 0.0), r_series)
        assert abs(h.values[0] - 2.3) < 1e-9

    def test_empty_positions_rejected(self):
        r_series = TimeSeries(timestamps=(0.0,), values=(0.7,))
        with pytest.raises(ValueError, match='drone_position_msgs 빈'):
            positions_to_h_series([], (0.0, 0.0, 0.0), r_series)

    def test_empty_r_series_rejected(self):
        r_series = TimeSeries(timestamps=(), values=())
        with pytest.raises(ValueError, match='r_series 빈'):
            positions_to_h_series(
                [(0.0, (1.0, 0.0, 0.0))], (0.0, 0.0, 0.0), r_series,
            )

    def test_non_monotonic_positions_clamped(self):
        # drone timestamp 역행은 clamp(jitter 흡수), raise 안 함 (2026-07-01 적발 버그).
        r_series = TimeSeries(timestamps=(0.0, 2.0), values=(0.7, 0.7))
        positions = [
            (1.0, (1.0, 0.0, 0.0)),
            (0.5, (1.0, 0.0, 0.0)),  # 역행 → clamp
        ]
        h = positions_to_h_series(positions, (0.0, 0.0, 0.0), r_series)
        assert len(h.timestamps) == 2
        assert h.timestamps[1] >= h.timestamps[0]  # 단조화됨


# ----------------------------------------------------------- extract_r_from_estimator_reports


def _make_report_payload(c_tilde: float) -> str:
    return json.dumps({
        'stamp_ns': 0, 'elapsed_s': 0.0, 'scenario_name': 'test',
        'segment_idx': 0,
        's1': 0.9, 's2': 0.9, 's3': 0.9,
        's1_absent': False, 's2_absent': False, 's3_absent': False,
        'c_raw': c_tilde, 'c_tilde': c_tilde, 'c_tilde_prev': c_tilde,
        'dot_c_max': 0.833, 'delta_c_clamped': False,
        'delta_c_requested': 0.0, 'delta_c_applied': 0.0,
    })


class TestExtractRFromEstimatorReports:
    def test_c_tilde_one_gives_r_min(self):
        """c_tilde = 1 → r = r_min."""
        reports = [(0.0, _make_report_payload(1.0))]
        r = extract_r_from_estimator_reports(reports, r_min=0.7, r_max=1.5)
        assert abs(r.values[0] - 0.7) < 1e-9

    def test_c_tilde_zero_gives_r_max(self):
        """c_tilde = 0 → r = r_max."""
        reports = [(0.0, _make_report_payload(0.0))]
        r = extract_r_from_estimator_reports(reports, r_min=0.7, r_max=1.5)
        assert abs(r.values[0] - 1.5) < 1e-9

    def test_c_tilde_half_gives_midpoint(self):
        """c_tilde = 0.5 → r = (r_min + r_max) / 2 = 1.1."""
        reports = [(0.0, _make_report_payload(0.5))]
        r = extract_r_from_estimator_reports(reports, r_min=0.7, r_max=1.5)
        assert abs(r.values[0] - 1.1) < 1e-9

    def test_multiple_reports_series(self):
        reports = [
            (0.0, _make_report_payload(1.0)),
            (1.0, _make_report_payload(0.5)),
            (2.0, _make_report_payload(0.0)),
        ]
        r = extract_r_from_estimator_reports(reports, r_min=0.7, r_max=1.5)
        assert r.timestamps == (0.0, 1.0, 2.0)
        assert abs(r.values[0] - 0.7) < 1e-9
        assert abs(r.values[1] - 1.1) < 1e-9
        assert abs(r.values[2] - 1.5) < 1e-9

    def test_empty_reports_rejected(self):
        with pytest.raises(ValueError, match='빈 list'):
            extract_r_from_estimator_reports([], r_min=0.7, r_max=1.5)

    def test_invalid_r_min_rejected(self):
        with pytest.raises(ValueError, match='r_min'):
            extract_r_from_estimator_reports(
                [(0.0, _make_report_payload(0.5))], r_min=-0.1, r_max=1.5,
            )

    def test_r_min_ge_r_max_rejected(self):
        with pytest.raises(ValueError, match='r_min < r_max'):
            extract_r_from_estimator_reports(
                [(0.0, _make_report_payload(0.5))], r_min=1.5, r_max=1.5,
            )

    def test_c_tilde_out_of_range_rejected(self):
        reports = [(0.0, _make_report_payload(1.5))]
        with pytest.raises(ValueError, match='c_tilde'):
            extract_r_from_estimator_reports(reports, r_min=0.7, r_max=1.5)

    def test_missing_c_tilde_key_raises(self):
        payload = json.dumps({'stamp_ns': 0})  # c_tilde 부재
        with pytest.raises(KeyError):
            extract_r_from_estimator_reports(
                [(0.0, payload)], r_min=0.7, r_max=1.5,
            )


# ----------------------------------------------------------- Tier 2 decision count


def _make_decision(decision: str, sigma: str = 'move_to') -> str:
    return json.dumps({
        'timestamp_ns': 0, 'sigma': sigma,
        'decision': decision, 'reason': 'test',
    })


class TestCountDecisions:
    """PR #110 review E-1 정정 — 통합 함수 (n_ask, n_total) atomic 반환.

    PR #304 리뷰 정정 (ADR-0032 amendment 2026-07-03) — n_ask 는 ``confirm``
    결정 count (gate_node 는 리터럴 ``ask_user`` 를 발행하지 않으므로 그 카운트는
    회귀 방지 목적으로 아래 test_literal_ask_user_string_rejected 만 남긴다).
    """

    def test_no_confirm(self):
        decisions = [
            _make_decision('accept'),
            _make_decision('accept'),
            _make_decision('reject'),
        ]
        n_ask, n_total = count_decisions(decisions)
        assert n_ask == 0
        assert n_total == 3

    def test_all_confirm(self):
        decisions = [_make_decision('confirm') for _ in range(5)]
        n_ask, n_total = count_decisions(decisions)
        assert n_ask == 5
        assert n_total == 5

    def test_mixed(self):
        decisions = [
            _make_decision('accept'),
            _make_decision('confirm'),
            _make_decision('reject'),
            _make_decision('confirm'),
            _make_decision('accept'),
        ]
        n_ask, n_total = count_decisions(decisions)
        assert n_ask == 2
        assert n_total == 5

    def test_empty(self):
        n_ask, n_total = count_decisions([])
        assert n_ask == 0
        assert n_total == 0

    def test_unknown_decision_rejected(self):
        """invalid decision 측 *first sample 측* schema 검증 측 raise — silent
        n_total count 측 회피 (E-1 정정 핵심).
        """
        decisions = [_make_decision('unknown')]
        with pytest.raises(ValueError, match='unknown Tier 2 decision'):
            count_decisions(decisions)

    def test_literal_ask_user_string_rejected(self):
        """회귀 방지 (PR #304 발견) — gate_node 는 리터럴 'ask_user' 를 발행하지
        않으므로 이는 이제 _ALLOWED_DECISIONS 밖. 종전엔 이 값을 n_ask 로 세어
        전 baseline n_ask 가 구조적으로 0 이 되는 결함이 있었다.
        """
        decisions = [_make_decision('ask_user')]
        with pytest.raises(ValueError, match='unknown Tier 2 decision'):
            count_decisions(decisions)

    def test_mixed_with_invalid_rejects_before_count(self):
        """*invalid 측 한 sample 만 있어도* schema 검증 측 atomic raise —
        n_total = len()=raw 측 silent invalid count 회피.
        """
        decisions = [
            _make_decision('accept'),
            _make_decision('unknown'),  # 두 번째 sample 측 invalid
            _make_decision('confirm'),
        ]
        with pytest.raises(ValueError, match='unknown Tier 2 decision'):
            count_decisions(decisions)

    def test_missing_decision_key_raises(self):
        decisions = [json.dumps({'timestamp_ns': 0})]
        with pytest.raises(KeyError):
            count_decisions(decisions)


# ----------------------------------------------------------- extract_loop_periods


class TestExtractLoopPeriods:
    def test_uniform_50ms(self):
        """50 ms period 5 sample → 4 periods 모두 0.05."""
        ts = [0.0, 0.050, 0.100, 0.150, 0.200]
        periods = extract_loop_periods(ts)
        assert len(periods) == 4
        for p in periods:
            assert abs(p - 0.050) < 1e-9

    def test_variable_periods(self):
        ts = [0.0, 0.045, 0.100, 0.160]
        periods = extract_loop_periods(ts)
        assert abs(periods[0] - 0.045) < 1e-9
        assert abs(periods[1] - 0.055) < 1e-9
        assert abs(periods[2] - 0.060) < 1e-9

    def test_two_sample_minimum(self):
        ts = [0.0, 0.050]
        periods = extract_loop_periods(ts)
        assert len(periods) == 1
        assert periods[0] == 0.050

    def test_single_sample_rejected(self):
        with pytest.raises(ValueError, match=r'sample.*2'):
            extract_loop_periods([0.0])

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match=r'sample.*2'):
            extract_loop_periods([])

    def test_jitter_inversion_clamped(self):
        # 레코더 jitter 역전(0.3 ms)은 clamp(해당 period 0), 크래시 안 함 (2026-07-01 적발 버그).
        periods = extract_loop_periods([0.0, 0.050, 0.0497, 0.100])
        assert len(periods) == 3
        assert all(p >= 0 for p in periods)
        assert abs(max(periods) - 0.050) < 1e-9  # τ_loop = max 무관

    def test_large_inversion_also_clamped(self):
        # 큰 역전도 clamp(순서 정렬, raise 안 함) — magnitude 무관 단일 정책.
        periods = extract_loop_periods([0.0, 0.050, 0.040])
        assert periods == pytest.approx([0.050, 0.000])


class TestGateRejectionRate:
    """ADR-0039 D4 — 게이트 거부율 (C3 정량). reject/total, 빈 list→None."""

    def test_empty_returns_none(self):
        assert gate_rejection_rate([]) is None

    def test_all_reject(self):
        decisions = [_make_decision('reject') for _ in range(4)]
        assert gate_rejection_rate(decisions) == 1.0

    def test_mixed_ratio(self):
        decisions = [
            _make_decision('accept'),
            _make_decision('reject'),
            _make_decision('confirm'),
            _make_decision('reject'),
        ]
        assert gate_rejection_rate(decisions) == 0.5  # 2 reject / 4

    def test_no_reject(self):
        decisions = [_make_decision('accept'), _make_decision('confirm')]
        assert gate_rejection_rate(decisions) == 0.0

    def test_unknown_decision_rejected(self):
        with pytest.raises(ValueError, match='unknown'):
            gate_rejection_rate([_make_decision('explode')])
