r"""waypoint → 포화 P-제어 속도 변환 — pure 로직 (rclpy 무관).

[ADR-0029](../../../docs/handover/decisions/0029-trial-integration-live-path.md)
블로커 3 — *의도해석기*/sigma_bridge 가 내는 *목표 지점(pose waypoint)* 을 *연속
속도 공칭 입력* 으로 바꾼다. paper §5 의 정형 보증은 단일적분기 속도 CBF-QP
($\dot p = u$, $u$=속도)이므로 nominal 도 *연속 속도* 여야 tier1 의 정형 보증된
필터가 매 tick 작동한다(1회성 pose 투영 아님). 도달 시 속도가 0 으로 수렴하므로
세션 44 S6 의 *멈추지 않는 등속도 접선 탈주* 도 발생하지 않는다(bounded).

좌표계: 입력·출력 모두 ENU world(local) 프레임. NED↔ENU 변환은 노드가 담당.
host venv 에서 rclpy 없이 단위 테스트.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


def ned_to_enu_position(x_ned: float, y_ned: float, z_ned: float) -> Tuple[float, float, float]:
    """PX4 NED 위치 → ENU. (g1_offboard.frame_conversions 와 동일 규약의 역변환.)"""
    return (y_ned, x_ned, -z_ned)


def waypoint_velocity(
    target: Tuple[float, float, float],
    pos: Tuple[float, float, float],
    k_p: float,
    u_max: float,
    arrival_radius_m: float = 0.0,
) -> Tuple[float, float, float]:
    r"""목표 지점으로 향하는 포화 비례(P) 제어 속도 (ENU).

    $v = \mathrm{sat}_{u_\text{max}}(k_p (p_\text{target} - p))$ — 방향 보존 포화
    (성분별 아닌 *크기* clamp). 도달 반경 안이면 영속도(정지) → 도달 후 잔류 진동·
    과주행 방지.

    Args:
        target: 목표 world ENU 좌표 [m].
        pos: 드론 현재 world ENU 좌표 [m].
        k_p: 비례 게인 [1/s] (양수). 거리 → 속도.
        u_max: 속도 크기 상한 [m/s] (양수, paper §5 $u_\text{max}$ 와 정합).
        arrival_radius_m: 이 반경 안이면 정지 [m] (≥0).

    Returns:
        ENU 속도 ``(vx, vy, vz)`` [m/s].

    Raises:
        ValueError: k_p ≤ 0 또는 u_max ≤ 0 또는 arrival_radius_m < 0.
    """
    if k_p <= 0.0:
        raise ValueError(f'k_p 는 양수여야 함: {k_p}')
    if u_max <= 0.0:
        raise ValueError(f'u_max 는 양수여야 함: {u_max}')
    if arrival_radius_m < 0.0:
        raise ValueError(f'arrival_radius_m 는 0 이상: {arrival_radius_m}')

    dx = target[0] - pos[0]
    dy = target[1] - pos[1]
    dz = target[2] - pos[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist <= arrival_radius_m or dist == 0.0:
        return (0.0, 0.0, 0.0)

    vx, vy, vz = k_p * dx, k_p * dy, k_p * dz
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    if speed > u_max:
        scale = u_max / speed
        vx, vy, vz = vx * scale, vy * scale, vz * scale
    return (vx, vy, vz)


def yaw_rate_to_target(
    target_yaw: float,
    current_yaw: float,
    k_yaw: float,
    yawrate_max: float,
) -> float:
    r"""목표 yaw 로 향하는 포화 비례(P) yaw 속도 [rad/s] (ENU, CCW 양수).

    [ADR-0031](../../../docs/handover/decisions/0031-inspect-vantage-automation.md)
    inspect vantage — 전방 +15° 하향 고정 카메라(짐벌 없음)가 대상을 프레임에
    담으려면 드론 동체 yaw 가 대상 클러스터를 향해야 한다. 목표 yaw 로의 각도차를
    $[-\pi, \pi]$ 로 wrap 해(최단 회전) 포화 P 제어한 yaw 속도를 낸다. 이 값은
    twist.angular.z 로 실려 tier1(yawspeed pass-through, CBF 는 yaw 무관)을 거쳐
    g1 의 yawspeed 로 전달된다 — 위치 속도 CBF 의 정형 보증과 직교.

    Args:
        target_yaw: 목표 yaw [rad] (ENU East 기준 CCW).
        current_yaw: 현재 드론 yaw [rad] (동일 규약).
        k_yaw: 비례 게인 [1/s] (양수).
        yawrate_max: yaw 속도 크기 상한 [rad/s] (양수).

    Returns:
        yaw 속도 [rad/s], 크기 ≤ ``yawrate_max``.

    Raises:
        ValueError: k_yaw ≤ 0 또는 yawrate_max ≤ 0.
    """
    if k_yaw <= 0.0:
        raise ValueError(f'k_yaw 는 양수여야 함: {k_yaw}')
    if yawrate_max <= 0.0:
        raise ValueError(f'yawrate_max 는 양수여야 함: {yawrate_max}')
    err = target_yaw - current_yaw
    # [-π, π] wrap — 최단 회전 방향.
    err = math.atan2(math.sin(err), math.cos(err))
    rate = k_yaw * err
    if rate > yawrate_max:
        return yawrate_max
    if rate < -yawrate_max:
        return -yawrate_max
    return rate


def quaternion_zw_to_yaw(z: float, w: float) -> Optional[float]:
    """평면 회전 quaternion (z, w) → yaw [rad] (ENU East 기준 CCW). all-zero 면 None.

    PoseStamped.orientation 에 yaw 만 인코딩한 목표 자세(x=y=0)를 복원한다
    (sigma_bridge_helpers.yaw_to_quaternion_zw 의 역, 동일 규약). all-zero
    (x=y=z=w=0)는 "yaw 의도 없음"(현 yaw 유지)이라 None 반환 — 이 경우 호출측은
    yaw 제어를 하지 않는다(이동 nominal 만).
    """
    if z == 0.0 and w == 0.0:
        return None
    return 2.0 * math.atan2(z, w)
