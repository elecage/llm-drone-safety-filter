"""bag_reader 단위 테스트 — 순수 변환(build_bag_inputs·ned_to_enu) + rosbag2 격리.

rosbag2_py I/O(read_bag)는 ROS 2 환경 전용이라 host venv 에서는 graceful 검증만.
순수 변환 로직(NED→ENU·episode 길이 산출)은 rosbag2 없이 전수 테스트한다.
"""

from __future__ import annotations

import pytest

from dataclasses import dataclass

from eval_runner.bag_reader import (
    HAS_ROSBAG2,
    build_bag_inputs,
    ned_to_enu,
    read_bag,
    stamp_to_s,
)
from eval_runner.bag_pipeline import BagInputs


# -------------------------------------------------------------------- NED → ENU


def test_ned_to_enu_axis_swap() -> None:
    """NED (x, y, z) → ENU (y, x, -z) — filter_node 규칙."""
    assert ned_to_enu((1.0, 2.0, 3.0)) == (2.0, 1.0, -3.0)


# ------------------------------------------------------------------- stamp_to_s


@dataclass
class _Stamp:
    """builtin_interfaces/Time duck (header.stamp) — rosbag2 없이 테스트."""

    sec: int
    nanosec: int


def test_stamp_to_s_combines_sec_nanosec() -> None:
    """header.stamp.sec + nanosec*1e-9 — setpoint τ_loop 가 쓰는 sim-time."""
    assert stamp_to_s(_Stamp(sec=12, nanosec=500_000_000)) == pytest.approx(12.5)


def test_stamp_to_s_20hz_period() -> None:
    """연속 stamp 차이 = 50 ms (20 Hz) — bag 기록 jitter 와 무관한 발행 주기."""
    t0 = stamp_to_s(_Stamp(sec=100, nanosec=0))
    t1 = stamp_to_s(_Stamp(sec=100, nanosec=50_000_000))
    assert t1 - t0 == pytest.approx(0.05)


def test_stamp_to_s_zero() -> None:
    """미설정(0,0) stamp → 0.0 (degenerate — 발행기 sim-time 미주입 신호)."""
    assert stamp_to_s(_Stamp(sec=0, nanosec=0)) == 0.0


def test_ned_to_enu_z_down_to_up() -> None:
    """NED z(아래 +) → ENU z(위 +): 부호 반전."""
    _, _, z_enu = ned_to_enu((0.0, 0.0, 5.0))
    assert z_enu == -5.0


# -------------------------------------------------------------------- build_bag_inputs


def _two_setpoints() -> list:
    return [0.0, 0.05, 0.1]


def test_build_basic_fields() -> None:
    bi = build_bag_inputs(
        drone_position_ned=[(0.0, (1.0, 2.0, 3.0)), (0.1, (1.1, 2.1, 3.1))],
        setpoint_timestamps_s=_two_setpoints(),
        estimator_report_json_strs=[(0.0, '{"c_tilde": 0.5}')],
        tier2_decision_json_strs=['{"decision": "proceed"}'],
        clock_timestamps_s=[0.0, 0.1],
    )
    assert isinstance(bi, BagInputs)
    # 드론 위치는 ENU 변환되어 들어간다.
    assert bi.drone_position_msgs[0] == (0.0, (2.0, 1.0, -3.0))
    assert bi.drone_position_msgs[1] == (0.1, (2.1, 1.1, -3.1))
    assert bi.setpoint_timestamps_s == _two_setpoints()


def test_json_strs_passthrough() -> None:
    """report (t, json) / decision (json) 그대로 전달 (bag_signals 입력 계약)."""
    bi = build_bag_inputs(
        drone_position_ned=[(0.0, (0.0, 0.0, 0.0)), (1.0, (0.0, 0.0, 0.0))],
        setpoint_timestamps_s=[0.0, 1.0],
        estimator_report_json_strs=[(0.5, '{"c_tilde": 0.7}')],
        tier2_decision_json_strs=['{"decision": "confirm"}'],
        clock_timestamps_s=[0.0, 1.0],
    )
    assert bi.estimator_report_json_strs == [(0.5, '{"c_tilde": 0.7}')]
    assert bi.tier2_decision_json_strs == ['{"decision": "confirm"}']


# -------------------------------------------------------------------- episode 길이


def test_episode_duration_override_wins() -> None:
    """명시 override(trial_meta wall_clock)가 bag span 보다 우선."""
    bi = build_bag_inputs(
        drone_position_ned=[(0.0, (0.0, 0.0, 0.0)), (1.0, (0.0, 0.0, 0.0))],
        setpoint_timestamps_s=[0.0, 1.0],
        estimator_report_json_strs=[],
        tier2_decision_json_strs=[],
        clock_timestamps_s=[0.0, 5.0],
        episode_duration_s=12.3,
    )
    assert bi.episode_duration_s == 12.3


def test_episode_duration_clock_span() -> None:
    """override 없으면 `/clock` span 사용."""
    bi = build_bag_inputs(
        drone_position_ned=[(0.0, (0.0, 0.0, 0.0))],
        setpoint_timestamps_s=[0.0, 2.0],
        estimator_report_json_strs=[],
        tier2_decision_json_strs=[],
        clock_timestamps_s=[1.0, 4.0],
    )
    assert bi.episode_duration_s == pytest.approx(3.0)


def test_episode_duration_fallback_all_timestamps() -> None:
    """clock 부재 시 전 토픽 timestamp span fallback."""
    bi = build_bag_inputs(
        drone_position_ned=[(0.5, (0.0, 0.0, 0.0))],
        setpoint_timestamps_s=[0.0, 2.0],
        estimator_report_json_strs=[(1.0, '{}')],
        tier2_decision_json_strs=[],
        clock_timestamps_s=[],
    )
    # all_ts = drone[0.5] + setpoint[0, 2] + report[1.0] → span 2.0 - 0.0
    assert bi.episode_duration_s == pytest.approx(2.0)


def test_episode_duration_unresolvable_raises() -> None:
    """clock·timestamp 모두 < 2 sample → 명확한 ValueError."""
    with pytest.raises(ValueError, match='산출 불가'):
        build_bag_inputs([], [], [], [], [])


# -------------------------------------------------------------------- BagInputs 검증 전파


def test_setpoint_min_two_enforced() -> None:
    """setpoint < 2 면 BagInputs.__post_init__ 가 거부 (latency 의미 정합)."""
    with pytest.raises(ValueError, match='최소 2 sample'):
        build_bag_inputs(
            drone_position_ned=[(0.0, (0.0, 0.0, 0.0))],
            setpoint_timestamps_s=[0.0],  # 1 sample
            estimator_report_json_strs=[],
            tier2_decision_json_strs=[],
            clock_timestamps_s=[0.0, 1.0],
        )


# -------------------------------------------------------------------- rosbag2 격리


@pytest.mark.skipif(HAS_ROSBAG2, reason='rosbag2_py 설치 환경 — host mock 케이스만')
def test_read_bag_without_rosbag2_raises() -> None:
    """host venv(rosbag2_py 미설치)에서 read_bag 은 명확한 RuntimeError."""
    with pytest.raises(RuntimeError, match='rosbag2_py 미설치'):
        read_bag('/tmp/nonexistent_bag')
