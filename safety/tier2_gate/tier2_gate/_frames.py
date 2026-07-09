"""tier2_gate 내부 — PX4 NED ↔ ROS ENU 좌표 변환 + 센서 정규화.

ADR-0011 D3 의 *사상* (axis swap + z 부호 반전) 을 tier2_gate 자체에서 구현 —
sim/g1_offboard 패키지에 의존하지 않기 위함. 변환 사상은 involution 이라
NED→ENU 와 ENU→NED 가 같은 함수.
"""

from __future__ import annotations

from typing import Tuple


def ned_to_enu(x_ned: float, y_ned: float, z_ned: float) -> Tuple[float, float, float]:
    """위치·속도 모두 동일한 축 사상 — y와 x 교환 + z 부호 반전.

    ENU↔NED 는 involution 이므로 본 함수가 두 방향 모두에 적용된다.
    """
    return (y_ned, x_ned, -z_ned)


def battery_remaining_to_pct(remaining: float) -> float:
    """PX4 BatteryStatus.remaining (0.0~1.0) → 백분율 (0~100).

    음수·NaN·1.0 초과 값은 clamp.
    """
    if remaining != remaining:  # NaN
        return 0.0
    return max(0.0, min(100.0, remaining * 100.0))
