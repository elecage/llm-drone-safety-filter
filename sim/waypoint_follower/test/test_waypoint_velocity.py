"""waypoint_velocity 단위 테스트 — rclpy 의존성 없이 pure 로직만 (ADR-0029 블로커 3).

노드 자체(rclpy timer/subscriber)는 colcon test(Docker) + Mac mini e2e 에서 검증.
"""

from __future__ import annotations

import math

import pytest

from waypoint_follower.waypoint_velocity import (
    ned_to_enu_position,
    quaternion_zw_to_yaw,
    waypoint_velocity,
    yaw_rate_to_target,
)


def _norm(v):
    return math.sqrt(sum(c * c for c in v))


class TestNedToEnuPosition:
    def test_swap_and_z_flip(self):
        assert ned_to_enu_position(1.0, 2.0, 3.0) == (2.0, 1.0, -3.0)

    def test_origin(self):
        assert ned_to_enu_position(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)


class TestWaypointVelocity:
    def test_direction_toward_target(self):
        # +x 목표 → +x 속도 (방향 보존).
        v = waypoint_velocity((2.0, 0.0, 0.0), (0.0, 0.0, 0.0), k_p=0.6, u_max=10.0)
        assert v[0] > 0 and v[1] == pytest.approx(0.0) and v[2] == pytest.approx(0.0)

    def test_proportional_below_saturation(self):
        # 거리 1m, k_p=0.6 → 0.6 m/s (포화 미달).
        v = waypoint_velocity((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), k_p=0.6, u_max=10.0)
        assert _norm(v) == pytest.approx(0.6)

    def test_saturation_caps_magnitude(self):
        # 먼 목표 → u_max 로 포화, 방향 보존.
        v = waypoint_velocity((100.0, 0.0, 0.0), (0.0, 0.0, 0.0), k_p=0.6, u_max=0.5)
        assert _norm(v) == pytest.approx(0.5)
        assert v[0] == pytest.approx(0.5)

    def test_saturation_preserves_direction_diagonal(self):
        # 대각 목표 → 크기 clamp 하되 방향(45°) 보존.
        v = waypoint_velocity((10.0, 10.0, 0.0), (0.0, 0.0, 0.0), k_p=1.0, u_max=0.5)
        assert _norm(v) == pytest.approx(0.5)
        assert v[0] == pytest.approx(v[1])

    def test_arrival_radius_stops(self):
        # 도달 반경 안 → 영속도 (과주행·진동 방지).
        v = waypoint_velocity((0.1, 0.0, 0.0), (0.0, 0.0, 0.0),
                              k_p=0.6, u_max=0.5, arrival_radius_m=0.15)
        assert v == (0.0, 0.0, 0.0)

    def test_at_target_zero(self):
        v = waypoint_velocity((1.0, 1.0, 1.0), (1.0, 1.0, 1.0), k_p=0.6, u_max=0.5)
        assert v == (0.0, 0.0, 0.0)

    def test_3d_component(self):
        v = waypoint_velocity((0.0, 0.0, 2.0), (0.0, 0.0, 0.0), k_p=0.6, u_max=10.0)
        assert v[2] == pytest.approx(1.2)

    def test_invalid_kp_raises(self):
        with pytest.raises(ValueError):
            waypoint_velocity((1, 0, 0), (0, 0, 0), k_p=0.0, u_max=0.5)

    def test_invalid_umax_raises(self):
        with pytest.raises(ValueError):
            waypoint_velocity((1, 0, 0), (0, 0, 0), k_p=0.6, u_max=0.0)

    def test_invalid_arrival_raises(self):
        with pytest.raises(ValueError):
            waypoint_velocity((1, 0, 0), (0, 0, 0), k_p=0.6, u_max=0.5,
                              arrival_radius_m=-1.0)


class TestYawRateToTarget:
    def test_zero_error_zero_rate(self):
        assert yaw_rate_to_target(0.5, 0.5, k_yaw=1.2, yawrate_max=0.8) == pytest.approx(0.0)

    def test_positive_error_ccw(self):
        # 목표가 현재보다 CCW(+) → 양의 yawspeed (포화 미달).
        r = yaw_rate_to_target(0.5, 0.0, k_yaw=1.0, yawrate_max=10.0)
        assert r == pytest.approx(0.5)

    def test_negative_error_cw(self):
        r = yaw_rate_to_target(0.0, 0.5, k_yaw=1.0, yawrate_max=10.0)
        assert r == pytest.approx(-0.5)

    def test_saturation_positive(self):
        r = yaw_rate_to_target(3.0, 0.0, k_yaw=1.0, yawrate_max=0.8)
        assert r == pytest.approx(0.8)

    def test_saturation_negative(self):
        r = yaw_rate_to_target(-3.0, 0.0, k_yaw=1.0, yawrate_max=0.8)
        assert r == pytest.approx(-0.8)

    def test_wrap_shortest_rotation(self):
        # 현재 yaw=−170°, 목표=+170° → 최단 회전은 +20°(CCW 아닌 −20°? wrap)
        # err = 170−(−170)=340° → wrap −20° → 음의 rate(CW 최단).
        cur = math.radians(-170.0)
        tgt = math.radians(170.0)
        r = yaw_rate_to_target(tgt, cur, k_yaw=1.0, yawrate_max=10.0)
        assert r == pytest.approx(math.radians(-20.0), abs=1e-9)

    def test_wrap_does_not_exceed_pi(self):
        # 임의 입력에 대해 |k_yaw·err| 의 err 은 [-π, π] 안.
        r = yaw_rate_to_target(math.radians(179.0), math.radians(-179.0),
                               k_yaw=1.0, yawrate_max=10.0)
        # err wrap = -2° → 음수, 작은 크기.
        assert r == pytest.approx(math.radians(-2.0), abs=1e-9)

    def test_invalid_k_yaw_raises(self):
        with pytest.raises(ValueError):
            yaw_rate_to_target(0.5, 0.0, k_yaw=0.0, yawrate_max=0.8)

    def test_invalid_yawrate_max_raises(self):
        with pytest.raises(ValueError):
            yaw_rate_to_target(0.5, 0.0, k_yaw=1.0, yawrate_max=0.0)


class TestQuaternionZwToYaw:
    def test_all_zero_is_none(self):
        # all-zero quaternion = yaw 의도 없음.
        assert quaternion_zw_to_yaw(0.0, 0.0) is None

    def test_identity_zero_yaw(self):
        # z=0, w=1 → yaw 0.
        assert quaternion_zw_to_yaw(0.0, 1.0) == pytest.approx(0.0)

    def test_roundtrip_manual_encoding(self):
        # 평면 yaw quaternion (z=sin(yaw/2), w=cos(yaw/2)) 왕복.
        for deg in (-170, -90, -10, 0, 10, 90, 170):
            yaw = math.radians(deg)
            z, w = math.sin(yaw / 2.0), math.cos(yaw / 2.0)
            back = quaternion_zw_to_yaw(z, w)
            assert back == pytest.approx(yaw, abs=1e-9)
