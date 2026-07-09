"""ENU↔NED 변환 unit test (ADR-0011 §D3).

변환은 단일 진입점이라 부호 실수가 즉시 드론 행동 이상으로 발현 → 명시 테스트.
"""

import math

import pytest

from g1_offboard.frame_conversions import (
    enu_position_to_ned,
    enu_velocity_to_ned,
    enu_yawrate_to_ned,
)


def test_east_velocity_becomes_north_in_ned():
    # ENU x(=E) = 1 → NED y(=E) = 1.
    assert enu_velocity_to_ned(1.0, 0.0, 0.0) == (0.0, 1.0, 0.0)


def test_north_velocity_becomes_north_in_ned():
    # ENU y(=N) = 1 → NED x(=N) = 1.
    assert enu_velocity_to_ned(0.0, 1.0, 0.0) == (1.0, 0.0, 0.0)


def test_up_velocity_flips_sign_in_ned():
    # ENU z(=Up) = 1 → NED z(=Down) = -1.
    assert enu_velocity_to_ned(0.0, 0.0, 1.0) == (0.0, 0.0, -1.0)


def test_combined_axes():
    # 3축 동시 — composition 검증.
    assert enu_velocity_to_ned(2.0, 3.0, 4.0) == (3.0, 2.0, -4.0)


def test_yawrate_flips_sign():
    # ENU z 위 회전 양 → NED z 아래 회전 음 (CCW remains CCW seen from above,
    # but axis direction flips).
    assert enu_yawrate_to_ned(0.5) == -0.5
    assert enu_yawrate_to_ned(-1.2) == pytest.approx(1.2)


def test_position_conversion_same_pattern():
    # 위치 변환도 velocity와 동일 축 사상 (z 부호 반전).
    assert enu_position_to_ned(1.0, 2.0, 3.0) == (2.0, 1.0, -3.0)


def test_zero_invariant():
    # 영벡터는 영벡터.
    assert enu_velocity_to_ned(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)
    assert enu_yawrate_to_ned(0.0) == 0.0


def test_round_trip_via_inverse():
    # ENU → NED → ENU (변환을 두 번 적용하면 z만 두 번 부호 반전 = identity).
    vx, vy, vz = 1.5, -2.3, 0.7
    ned = enu_velocity_to_ned(vx, vy, vz)
    # NED를 다시 ENU로 보려면 같은 사상이 자체 inverse (축 swap + z flip 모두 involution).
    roundtrip = enu_velocity_to_ned(ned[0], ned[1], ned[2])
    assert roundtrip == pytest.approx((vx, vy, vz))


def test_no_nan_introduced_on_finite_input():
    out = enu_velocity_to_ned(1.0, 2.0, 3.0)
    assert all(not math.isnan(v) for v in out)
