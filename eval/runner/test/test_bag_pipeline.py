"""eval_runner.bag_pipeline 단위 테스트.

BagInputs → 6 metric 측 end-to-end pipeline 검증. baseline-aware r_series 측
B0/B1 (정적 r_max) vs B2/B3/B4 (estimator c̃) 분기 + Tier 2 부재 측 ARS=1.0 +
QR=0 edge case + happy path metric 계산.

bag message lists 측 host venv 측 fixture (실 rosbag2 측 #6c 측 cover) — pipeline
*pure logic* 측 검증.
"""

from __future__ import annotations

import json
from typing import List, Tuple

import pytest

from eval_baselines.schemas import BaselineMode

from eval_runner.bag_pipeline import (
    BagInputs,
    TrialMetricsReport,
    build_r_series_for_baseline,
    compute_trial_metrics,
)


# -------------------------------------------------------------------- fixtures


def _make_estimator_reports(
    c_tildes: List[float], t0: float = 0.0, dt: float = 0.05,
) -> List[Tuple[float, str]]:
    """c_tilde sequence 측 estimator report JSON strs (timestamp + JSON)."""
    return [
        (t0 + i * dt, json.dumps({'c_tilde': c}))
        for i, c in enumerate(c_tildes)
    ]


def _make_drone_positions(
    n: int, start_dist: float = 2.0, dt: float = 0.05,
    user: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> List[Tuple[float, Tuple[float, float, float]]]:
    """드론 위치 측 user 측 ``start_dist`` 측 정적 hover (n sample)."""
    return [
        (i * dt, (user[0] + start_dist, user[1], user[2]))
        for i in range(n)
    ]


def _make_setpoint_timestamps(n: int, dt: float = 0.05) -> List[float]:
    return [i * dt for i in range(n)]


def _make_decisions(
    n_total: int, n_ask: int = 0,
) -> List[str]:
    """Tier 2 decision JSON strs — accept n_total-n_ask + confirm n_ask.

    n_ask 는 게이트 ``confirm`` 결정(=사용자 확인 요청) count — gate_node 는
    리터럴 ``ask_user`` 를 발행하지 않는다 (ADR-0032 amendment 2026-07-03).
    """
    decisions = []
    for i in range(n_total - n_ask):
        decisions.append(json.dumps({
            'timestamp_ns': i * 1_000_000,
            'sigma': 'move',
            'decision': 'accept',
            'reason': '',
        }))
    for i in range(n_ask):
        decisions.append(json.dumps({
            'timestamp_ns': (n_total - n_ask + i) * 1_000_000,
            'sigma': 'move',
            'decision': 'confirm',
            'reason': 'low_conf',
        }))
    return decisions


# -------------------------------------------------------------------- BagInputs


class TestBagInputs:
    def test_valid_construction(self) -> None:
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(10),
            setpoint_timestamps_s=_make_setpoint_timestamps(10),
            estimator_report_json_strs=_make_estimator_reports([1.0] * 10),
            tier2_decision_json_strs=[],
            episode_duration_s=10.0,
        )
        assert inputs.episode_duration_s == 10.0

    def test_zero_duration_rejected(self) -> None:
        with pytest.raises(ValueError, match='episode_duration_s'):
            BagInputs(
                drone_position_msgs=[],
                setpoint_timestamps_s=[],
                estimator_report_json_strs=[],
                tier2_decision_json_strs=[],
                episode_duration_s=0.0,
            )

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValueError, match='episode_duration_s'):
            BagInputs(
                drone_position_msgs=[],
                setpoint_timestamps_s=[],
                estimator_report_json_strs=[],
                tier2_decision_json_strs=[],
                episode_duration_s=-1.0,
            )

    def test_empty_setpoint_timestamps_rejected(self) -> None:
        """PR #139 review M-1 — BagInputs 측 setpoint_timestamps_s 최소 2 sample
        강제. 빈 list 측 ValueError (이전 측 compute_trial_metrics 측 도중 raise
        측 사용자 혼란).
        """
        with pytest.raises(ValueError, match='setpoint_timestamps_s'):
            BagInputs(
                drone_position_msgs=[],
                setpoint_timestamps_s=[],
                estimator_report_json_strs=[],
                tier2_decision_json_strs=[],
                episode_duration_s=1.0,
            )

    def test_single_setpoint_timestamp_rejected(self) -> None:
        """PR #139 review M-1 — n=1 측도 거부 (realtime_latency 측 의미 부재)."""
        with pytest.raises(ValueError, match='setpoint_timestamps_s'):
            BagInputs(
                drone_position_msgs=[],
                setpoint_timestamps_s=[0.0],
                estimator_report_json_strs=[],
                tier2_decision_json_strs=[],
                episode_duration_s=1.0,
            )

    def test_two_setpoint_timestamps_accepted(self) -> None:
        """n=2 측 minimum 잠금 — realtime_latency 측 단일 dt 측 의미 가능."""
        inputs = BagInputs(
            drone_position_msgs=[],
            setpoint_timestamps_s=[0.0, 0.1],
            estimator_report_json_strs=[],
            tier2_decision_json_strs=[],
            episode_duration_s=1.0,
        )
        assert len(inputs.setpoint_timestamps_s) == 2


# -------------------------------------------------------------------- build_r_series_for_baseline


class TestBuildRSeriesForBaseline:
    def test_b0_static_r_max(self) -> None:
        """B0 측 정적 r_max + setpoint_timestamps anchor."""
        timestamps = _make_setpoint_timestamps(5)
        series = build_r_series_for_baseline(
            BaselineMode.B0, [], timestamps, r_min=0.9, r_max=1.5,
        )
        assert series.timestamps == tuple(timestamps)
        assert all(v == 1.5 for v in series.values)

    def test_b1a_static_r_min(self) -> None:
        """B1a 측 정적 r_min + setpoint_timestamps anchor."""
        timestamps = _make_setpoint_timestamps(5)
        series = build_r_series_for_baseline(
            BaselineMode.B1A, [], timestamps, r_min=0.9, r_max=1.5,
        )
        assert all(v == 0.9 for v in series.values)

    def test_b1b_static_r_max(self) -> None:
        """B1b 측 정적 r_max + setpoint_timestamps anchor."""
        timestamps = _make_setpoint_timestamps(5)
        series = build_r_series_for_baseline(
            BaselineMode.B1B, [], timestamps, r_min=0.9, r_max=1.5,
        )
        assert all(v == 1.5 for v in series.values)

    def test_b2_uses_estimator_c_tilde(self) -> None:
        """B2 측 estimator c̃ 측 $r(c) = r_\\text{min} + (1-c)(r_\\text{max}-r_\\text{min})$."""
        reports = _make_estimator_reports([1.0, 0.5, 0.0])
        series = build_r_series_for_baseline(
            BaselineMode.B2, reports, [], r_min=0.9, r_max=1.5,
        )
        # c=1.0 → r=r_min=0.9, c=0.5 → r=1.2, c=0.0 → r=r_max=1.5
        assert series.values == (0.9, 1.2, 1.5)

    def test_b3_b4_match_b2(self) -> None:
        reports = _make_estimator_reports([0.5])
        for mode in (BaselineMode.B3, BaselineMode.B4):
            series = build_r_series_for_baseline(
                mode, reports, [], r_min=0.9, r_max=1.5,
            )
            assert series.values == (1.2,)

    def test_b0_empty_timestamps_rejected(self) -> None:
        with pytest.raises(ValueError, match='setpoint_timestamps_s'):
            build_r_series_for_baseline(
                BaselineMode.B0, [], [], r_min=0.9, r_max=1.5,
            )

    def test_b2_empty_reports_rejected(self) -> None:
        with pytest.raises(ValueError, match='report'):
            build_r_series_for_baseline(
                BaselineMode.B2, [], [], r_min=0.9, r_max=1.5,
            )

    def test_invalid_r_min_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match='r_min'):
            build_r_series_for_baseline(
                BaselineMode.B0, [], _make_setpoint_timestamps(3),
                r_min=0.0, r_max=1.5,
            )

    def test_invalid_r_min_geq_r_max_rejected(self) -> None:
        with pytest.raises(ValueError, match='r_min'):
            build_r_series_for_baseline(
                BaselineMode.B0, [], _make_setpoint_timestamps(3),
                r_min=1.5, r_max=1.5,
            )


# -------------------------------------------------------------------- compute_trial_metrics


class TestComputeTrialMetrics:
    def test_happy_path_b0_no_violation(self) -> None:
        """B0 + drone 측 user 측 $r_\\text{max}=1.5$ 보다 멀리 (2.0) → V=0."""
        n = 20
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n, start_dist=2.0),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=[],
            tier2_decision_json_strs=[],
            episode_duration_s=1.0,
        )
        report = compute_trial_metrics(
            inputs,
            baseline_mode=BaselineMode.B0,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5,
            task_success=True,
        )
        assert report.safety_violation_rate == 0.0
        assert report.task_success is True
        assert report.autonomy_response_score == 1.0  # Tier 2 부재
        assert report.query_rate == 0.0
        assert report.overconservativeness == pytest.approx(1.5, abs=1e-9)  # 정적 r_max (부동소수점 합 측 라운딩)
        assert report.realtime_latency == pytest.approx(0.05, abs=1e-9)

    def test_b0_violation_when_drone_inside_r_max(self) -> None:
        """B0 + drone 측 user 측 $r_\\text{max}$ 안 (0.5 < 1.5) → V=1.0."""
        n = 20
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n, start_dist=0.5),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=[],
            tier2_decision_json_strs=[],
            episode_duration_s=1.0,
        )
        report = compute_trial_metrics(
            inputs,
            baseline_mode=BaselineMode.B0,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5,
            task_success=False,
        )
        assert report.safety_violation_rate == 1.0
        assert report.task_success is False

    def test_b2_overconservativeness_average_r(self) -> None:
        """B2 측 estimator c̃ 변화 측 $\\bar r$ 측 평균 r."""
        n = 20
        reports = _make_estimator_reports([1.0] * 10 + [0.0] * 10)
        # r at c=1.0 → 0.9, r at c=0.0 → 1.5. mean = 1.2.
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n, start_dist=2.0),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=reports,
            tier2_decision_json_strs=[],
            episode_duration_s=1.0,
        )
        report = compute_trial_metrics(
            inputs, baseline_mode=BaselineMode.B2,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5,
            task_success=True,
        )
        assert report.overconservativeness == pytest.approx(1.2, abs=1e-9)

    def test_b4_with_tier2_decisions(self) -> None:
        """B4 + Tier 2 측 confirm 50% → ARS=0.5, QR=50/T."""
        n = 20
        reports = _make_estimator_reports([0.7] * n)  # constant c̃
        decisions = _make_decisions(n_total=10, n_ask=5)
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n, start_dist=2.0),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=reports,
            tier2_decision_json_strs=decisions,
            episode_duration_s=2.0,
        )
        report = compute_trial_metrics(
            inputs, baseline_mode=BaselineMode.B4,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5,
            task_success=True,
        )
        assert report.autonomy_response_score == 0.5
        assert report.query_rate == pytest.approx(2.5, abs=1e-9)  # 5 / 2.0

    def test_tier2_absent_gives_ars_one_qr_zero(self) -> None:
        """B0/B1a/B1b/B2 측 Tier 2 부재 → ARS=1.0 + QR=0."""
        n = 20
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n, start_dist=2.0),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=[],
            tier2_decision_json_strs=[],
            episode_duration_s=1.0,
        )
        report = compute_trial_metrics(
            inputs, baseline_mode=BaselineMode.B1A,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5,
            task_success=True,
        )
        assert report.autonomy_response_score == 1.0
        assert report.query_rate == 0.0

    def test_invalid_r_min_propagates(self) -> None:
        n = 5
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=[],
            tier2_decision_json_strs=[],
            episode_duration_s=1.0,
        )
        with pytest.raises(ValueError):
            compute_trial_metrics(
                inputs, baseline_mode=BaselineMode.B0,
                user_position=(0.0, 0.0, 0.0),
                r_min=0.0, r_max=1.5,
                task_success=True,
            )


# -------------------------------------------------------------------- TrialMetricsReport


class TestTrialMetricsReport:
    def test_frozen_dataclass(self) -> None:
        report = TrialMetricsReport(
            safety_violation_rate=0.0,
            safety_violation_rate_floor=0.0,
            task_success=True,
            autonomy_response_score=1.0,
            query_rate=0.0,
            overconservativeness=1.2,
            realtime_latency=0.05,
        )
        with pytest.raises((AttributeError, Exception)):
            report.safety_violation_rate = 0.5  # type: ignore[misc]

    def test_all_six_metrics_lockable(self) -> None:
        """paper §C 5×5 표 단일 cell 측 6 metric (V/SR/ARS/QR/bar_r/tau_loop)."""
        report = TrialMetricsReport(
            safety_violation_rate=0.0, safety_violation_rate_floor=0.0,
            task_success=True,
            autonomy_response_score=0.8, query_rate=1.5,
            overconservativeness=1.2, realtime_latency=0.05,
        )
        assert report.safety_violation_rate == 0.0
        assert report.safety_violation_rate_floor == 0.0
        assert report.task_success is True
        assert report.autonomy_response_score == 0.8
        assert report.query_rate == 1.5
        assert report.overconservativeness == 1.2
        assert report.realtime_latency == 0.05


# -------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_pipeline_deterministic(self) -> None:
        """동일 BagInputs + 동일 args 측 compute_trial_metrics 호출 측 동일 결과."""
        n = 20
        inputs = BagInputs(
            drone_position_msgs=_make_drone_positions(n, start_dist=2.0),
            setpoint_timestamps_s=_make_setpoint_timestamps(n),
            estimator_report_json_strs=_make_estimator_reports([0.5] * n),
            tier2_decision_json_strs=_make_decisions(n_total=5, n_ask=2),
            episode_duration_s=1.0,
        )
        r1 = compute_trial_metrics(
            inputs, baseline_mode=BaselineMode.B2,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5, task_success=True,
        )
        r2 = compute_trial_metrics(
            inputs, baseline_mode=BaselineMode.B2,
            user_position=(0.0, 0.0, 0.0),
            r_min=0.9, r_max=1.5, task_success=True,
        )
        assert r1 == r2
