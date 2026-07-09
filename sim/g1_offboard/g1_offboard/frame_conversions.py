"""ENU↔NED 변환 utility — ADR-0011 D3 단일 진입점.

ROS 2 내부 토픽은 모두 ENU (East-North-Up), PX4 인터페이스는 모두 NED
(North-East-Down). 두 좌표계 간 변환은 본 모듈 *한 곳* 에서만 처리한다 — 부호 실수가
즉시 드론 행동 이상으로 발현되므로 단일 진입점 + unit test로 격리.

변환식 (ADR-0011 §D3):

  v_x_NED =  v_y_ENU
  v_y_NED =  v_x_ENU
  v_z_NED = -v_z_ENU
  yawspeed_NED = -omega_z_ENU

위치(point) 변환도 동일한 축 사상을 따른다 (z만 부호 반전).
"""

from __future__ import annotations

from typing import Tuple


def enu_velocity_to_ned(vx_enu: float, vy_enu: float, vz_enu: float) -> Tuple[float, float, float]:
    """Linear velocity (3축) ENU → NED 변환."""
    return (vy_enu, vx_enu, -vz_enu)


def enu_yawrate_to_ned(omega_z_enu: float) -> float:
    """Yaw rate (z축 angular velocity) ENU → NED 변환.

    ENU의 z축이 위쪽, NED의 z축이 아래쪽 → 회전 부호 반전.
    """
    return -omega_z_enu


def enu_position_to_ned(x_enu: float, y_enu: float, z_enu: float) -> Tuple[float, float, float]:
    """위치 (3축) ENU → NED 변환. 본 G1 스코프에선 직접 사용 안 함
    (velocity setpoint만 권위, ADR-0011 D2). 디버깅·로깅용으로 제공.
    """
    return (y_enu, x_enu, -z_enu)
