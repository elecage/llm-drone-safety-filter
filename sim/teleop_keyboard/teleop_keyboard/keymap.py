"""Keyboard → ENU velocity normalized lookup — rclpy 측 *비의존* (host venv test 가능).

teleop_keyboard_node.py 측 *raw stdin termios* 측 *별 단위 test 어려움* 측 —
본 모듈 측 *static dict + helper* 측 host venv pytest 측 *분리 검증* 가능.

## ENU 정합

| 키 | 동작 | velocity (normalized) |
|---|---|---|
| W / S | forward / backward | +x / -x |
| A / D | left / right | +y / -y |
| R / F | up / down | +z / -z |
| Q / E | yaw left / right | +omega_z / -omega_z |
| space | stop | (0, 0, 0, 0) |

normalized 측 ∈ {-1.0, 0.0, 1.0} 측 *실 velocity* 측 caller 측 linear_speed +
angular_speed 측 scale 곱 (teleop_keyboard_node._poll_stdin 측 사용).
"""

from __future__ import annotations

from typing import Dict, Tuple


# (vx, vy, vz, omega_z) — normalized ∈ {-1.0, 0.0, 1.0}.
KEYMAP: Dict[str, Tuple[float, float, float, float]] = {
    'w': ( 1.0,  0.0,  0.0,  0.0),  # forward
    's': (-1.0,  0.0,  0.0,  0.0),  # backward
    'a': ( 0.0,  1.0,  0.0,  0.0),  # left
    'd': ( 0.0, -1.0,  0.0,  0.0),  # right
    'r': ( 0.0,  0.0,  1.0,  0.0),  # up
    'f': ( 0.0,  0.0, -1.0,  0.0),  # down
    'q': ( 0.0,  0.0,  0.0,  1.0),  # yaw left
    'e': ( 0.0,  0.0,  0.0, -1.0),  # yaw right
    ' ': ( 0.0,  0.0,  0.0,  0.0),  # space = stop
}
