"""_control.py compute_velocity P-controller 단위 테스트."""

from __future__ import annotations

import math

import pytest

from tier2_gate._control import compute_velocity


KP = 0.5
MAX_SPEED = 0.3
ARRIVAL = 0.1


def test_zero_at_goal():
    """drone == goal → zero velocity (arrival 이전)."""
    v = compute_velocity((1.0, 1.0, 1.0), (1.0, 1.0, 1.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    assert v == (0.0, 0.0, 0.0)


def test_within_arrival_threshold_zero():
    """drone 이 arrival 안 → zero."""
    v = compute_velocity((1.0, 0.0, 0.0), (0.95, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    assert v == (0.0, 0.0, 0.0)


def test_just_outside_arrival_proportional():
    """arrival 직후 → kp 비례 (clamp 안 됨)."""
    v = compute_velocity((1.0, 0.0, 0.0), (0.8, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    # error = 0.2, vx = 0.5 * 0.2 = 0.1 (max_speed 0.3 이하)
    assert v == pytest.approx((0.1, 0.0, 0.0))


def test_far_from_goal_max_speed_clamped():
    """멀리 — max_speed clamp."""
    v = compute_velocity((10.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    # raw vx = 0.5 * 10 = 5.0, norm clamp to 0.3
    assert v == pytest.approx((MAX_SPEED, 0.0, 0.0))


def test_sign_follows_error_vector():
    """goal 이 음수 방향이면 velocity 도 음수."""
    v = compute_velocity((-1.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    assert v[0] < 0 and v[1] == 0.0 and v[2] == 0.0


def test_3d_diagonal_velocity_norm_clamped():
    """3D 대각선 — 노름이 max_speed 와 같음."""
    v = compute_velocity((10.0, 10.0, 10.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    norm = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    assert norm == pytest.approx(MAX_SPEED)
    # 등방 — 세 성분 같음 (부호도 같음)
    assert v[0] == pytest.approx(v[1])
    assert v[1] == pytest.approx(v[2])


def test_close_proportional_velocity_under_max():
    """근거리에서 (kp * error) 가 max_speed 미만이면 clamp 안 함."""
    v = compute_velocity((0.5, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    # error = 0.5, vx = 0.25 < max_speed 0.3
    assert v == pytest.approx((0.25, 0.0, 0.0))


def test_arrival_threshold_is_inclusive_outside():
    """distance == arrival_threshold 는 strict < 라 still moving."""
    # distance = arrival = 0.1 — strict < arrival 아니므로 P 동작
    v = compute_velocity((0.1, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)
    # vx = 0.5 * 0.1 = 0.05 — but distance == arrival → check 코드 의도.
    # _control.py 에서 `if distance < arrival_threshold` 이므로 == 는 통과 → 움직임.
    assert v[0] > 0


def test_max_speed_zero_returns_zero():
    """max_speed = 0 (정지 강제) → arrival 밖에서도 zero."""
    v = compute_velocity((10.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=0.0, arrival_threshold=ARRIVAL)
    assert v == (0.0, 0.0, 0.0)


# ---- N2 fix — invalid param fail-fast ----

def test_negative_kp_rejected():
    """음수 kp → AssertionError (velocity 부호 반전 silent 발산 방지)."""
    with pytest.raises(AssertionError):
        compute_velocity((1.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=-0.5, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)


def test_zero_kp_rejected():
    """kp=0 → AssertionError (motion 없음을 명시적으로 표현하려면 max_speed=0 사용)."""
    with pytest.raises(AssertionError):
        compute_velocity((1.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=0.0, max_speed=MAX_SPEED, arrival_threshold=ARRIVAL)


def test_negative_max_speed_rejected():
    with pytest.raises(AssertionError):
        compute_velocity((1.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=-0.1, arrival_threshold=ARRIVAL)


def test_negative_arrival_threshold_rejected():
    with pytest.raises(AssertionError):
        compute_velocity((1.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         kp=KP, max_speed=MAX_SPEED, arrival_threshold=-0.01)
