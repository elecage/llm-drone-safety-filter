"""tier2_gate 내부 — dispatch_to_tier1 의 P-controller (pure-Python).

목표 위치까지의 오차 벡터에 비례 게인 ``kp`` 곱해 ENU velocity 산출.
속도는 ``max_speed`` 로 norm clamp. 도착 threshold 안에선 zero velocity.
ROS 의존 없음 — _control.py 는 pure function 으로 격리, 노드는 wiring 만.
"""

from __future__ import annotations

from typing import Sequence, Tuple

from tier2_gate._geom import l2


def compute_velocity(
    goal: Sequence[float],
    drone_pos: Sequence[float],
    *,
    kp: float,
    max_speed: float,
    arrival_threshold: float,
) -> Tuple[float, float, float]:
    """3D ENU 좌표에서 (goal - drone_pos) 비례 velocity. 도착 시 zero.

    파라미터:
    - goal, drone_pos: 길이 3 시퀀스 (ENU 좌표 [m]).
    - kp: 비례 게인 [1/s] — 양수 (음수 시 velocity 부호 반전으로 발산).
    - max_speed: velocity 노름 상한 [m/s] — ≥ 0 (보통 ADR-0013 D2 max_speed_hi (0.5)).
        max_speed=0 은 정지 강제 (방어적 허용).
    - arrival_threshold: 이 거리 안에선 zero velocity [m] — ≥ 0.

    Invalid input (음수 kp/max_speed/arrival_threshold) 은 AssertionError —
    launch param 오타나 misconfiguration 을 silent 가 아닌 fail-fast 로 검출.
    """
    assert kp > 0.0, f'kp={kp} 양수여야 함 (음수면 velocity 부호 반전)'
    assert max_speed >= 0.0, f'max_speed={max_speed} ≥ 0 (0 은 정지 강제)'
    assert arrival_threshold >= 0.0, f'arrival_threshold={arrival_threshold} ≥ 0'

    ex = float(goal[0]) - float(drone_pos[0])
    ey = float(goal[1]) - float(drone_pos[1])
    ez = float(goal[2]) - float(drone_pos[2])
    distance = l2(goal, drone_pos)

    if distance < arrival_threshold:
        return (0.0, 0.0, 0.0)

    vx, vy, vz = kp * ex, kp * ey, kp * ez
    speed = (vx * vx + vy * vy + vz * vz) ** 0.5
    if speed > max_speed:
        scale = max_speed / speed
        vx *= scale
        vy *= scale
        vz *= scale
    return (vx, vy, vz)
