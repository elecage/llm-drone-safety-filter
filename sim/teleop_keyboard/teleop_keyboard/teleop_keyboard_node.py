"""Manual keyboard teleop — WASD/RF/QE ENU velocity → TwistStamped publish.

ADR-0005 D3 측 intent-agnostic nominal source — g2_waypoint_player 측 *대신*
측 *수동 조종* 측면. tier1_filter 측 nominal 측 그대로 통과 (B0 측 passthrough,
B1/B2 측 CBF brake).

## 키맵 (드론 직관, ENU 정합)

| 키 | 동작 | velocity |
|---|---|---|
| **W** | forward | +x m/s (ENU East) |
| **S** | backward | -x m/s |
| **A** | left | +y m/s (ENU North) |
| **D** | right | -y m/s |
| **R** | up | +z m/s |
| **F** | down | -z m/s |
| **Q** | yaw left | +z rad/s |
| **E** | yaw right | -z rad/s |
| **space** | 정지 (zero velocity) | hover |
| **Ctrl-C** | 종료 | — |

## ENU frame 정합 주의

본 노드 측 키맵 측 *드론 frame 측면* 측 *직관*:
- W = "forward" = 사용자 측 화면 *위쪽 방향* — *ENU world frame* 측 +x (East)
- A = "left" = 사용자 측 *왼쪽* — *ENU world frame* 측 +y (North)

즉 *드론 spawn 측 yaw=0* (East 측 향함) 가정. yaw 측 회전 시 측 *world frame +x*
측 *body frame +x* 측 mismatch 발생 — 본 노드 측 *world frame ENU* 측 publish
측 *드론 측 자동 yaw correct* 측 G1/PX4 측 책임.

## 키 timeout 측 hover

키 누른 *마지막 시각* 측 `key_timeout_s` (default 0.5 s) 초과 측 자동 zero
velocity 측 publish (안전 측 *키 떼는 즉시* hover). standard teleop_twist_keyboard
측 동일 패턴.

## 사용

```bash
# Docker 컨테이너 측 별 Terminal 측 interactive tty (-it):
docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \\
    "cd /workspace && source install/setup.bash && \\
     ros2 run teleop_keyboard teleop_keyboard_node"
```

up.sh 측 *자동 시작 안 함* — 사용자 측 별 명령 측 직접 시작 (interactive
stdin 측 docker exec -it 필수).
"""

from __future__ import annotations

import select
import sys
import termios
import time
import tty

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import TwistStamped

from teleop_keyboard.keymap import KEYMAP


_HELP_TEXT = """
============================================================
Manual keyboard teleop — WASD ENU velocity (TwistStamped)
============================================================
  W / S       forward / backward  (+x / -x m/s, ENU East)
  A / D       left / right        (+y / -y m/s, ENU North)
  R / F       up / down           (+z / -z m/s)
  Q / E       yaw left / right    (+/- omega_z rad/s)
  space       stop (hover, zero velocity)
  Ctrl-C      quit

linear_speed = {lin:.2f} m/s | angular_speed = {ang:.2f} rad/s
key_timeout_s = {timeout:.2f} s | publish_rate_hz = {rate:.1f}
output_topic = {topic}
============================================================
"""


class TeleopKeyboardNode(Node):
    """termios raw stdin 측 키 → ENU velocity TwistStamped publish."""

    def __init__(self) -> None:
        super().__init__('teleop_keyboard_node')

        # parameters — tier1 u_max=0.5 m/s 정합 (default safety).
        self.declare_parameter('output_topic', '/cmd/trajectory_setpoint_nominal')
        self.declare_parameter('frame_id', 'world')
        self.declare_parameter('linear_speed', 0.5)   # m/s, tier1 u_max 정합
        self.declare_parameter('angular_speed', 0.5)  # rad/s
        self.declare_parameter('publish_rate_hz', 20.0)
        self.declare_parameter('key_timeout_s', 0.5)

        self._output_topic = self.get_parameter('output_topic').value
        self._frame_id = self.get_parameter('frame_id').value
        self._linear_speed = float(self.get_parameter('linear_speed').value)
        self._angular_speed = float(self.get_parameter('angular_speed').value)
        self._publish_rate_hz = float(self.get_parameter('publish_rate_hz').value)
        self._key_timeout_s = float(self.get_parameter('key_timeout_s').value)

        self._publisher = self.create_publisher(TwistStamped, self._output_topic, 10)

        # 현 velocity 상태 + 마지막 key 시각 (timeout 측 hover 판정).
        self._vx = 0.0
        self._vy = 0.0
        self._vz = 0.0
        self._omega_z = 0.0
        self._last_key_time = time.monotonic()

        # termios 측 raw stdin — original 측 보존 + atexit 측 restore. stdin 측
        # *non-tty* (예: `docker exec` 측 `-it` 없이 시작) 측 termios.error 측
        # graceful exit + 사용자 안내 (`docker exec -it ...` 측 필수 명시).
        try:
            self._original_termios = termios.tcgetattr(sys.stdin)
        except termios.error as e:
            raise RuntimeError(
                "teleop_keyboard 측 stdin 측 *interactive tty* 측 필수 — "
                "현 stdin 측 non-tty 측 'termios.error: {}'. Docker exec 측 "
                "`-it` flag 측 *반드시* 추가 의무. 예:\n"
                "  docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh "
                "bash -c \\\n"
                "    \"cd /workspace && source install/setup.bash && \\\n"
                "     ros2 run teleop_keyboard teleop_keyboard_node\"".format(e)
            ) from e
        tty.setcbreak(sys.stdin.fileno())  # non-canonical, 1 char at a time

        # publish timer (publish_rate_hz). 키 timeout 측 zero velocity.
        period_s = 1.0 / self._publish_rate_hz
        self._publish_timer = self.create_timer(period_s, self._publish_current)

        # stdin poll timer — 키 입력 측 capture + velocity update.
        # publish_rate 측 동일 주기 측 충분 (실 키 입력 측 sparse).
        self._poll_timer = self.create_timer(period_s, self._poll_stdin)

        print(_HELP_TEXT.format(
            lin=self._linear_speed,
            ang=self._angular_speed,
            timeout=self._key_timeout_s,
            rate=self._publish_rate_hz,
            topic=self._output_topic,
        ), flush=True)

    def restore_terminal(self) -> None:
        """termios 측 원상복구 — quit 또는 exception 시 호출 의무. non-tty 측
        `__init__` 측 raise 측 `_original_termios` 측 unset — hasattr 가드.
        """
        if hasattr(self, '_original_termios'):
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._original_termios)

    def _poll_stdin(self) -> None:
        """non-blocking stdin poll — 키 입력 측 capture + velocity update."""
        # select 측 50 ms timeout 측 non-blocking poll. publish_rate 측 동일 주기
        # 측 충분 — 50ms 측 키 입력 측 sparse 측 missed key 측 nullable.
        ready, _, _ = select.select([sys.stdin], [], [], 0.0)
        if not ready:
            return
        ch = sys.stdin.read(1)
        ch_lower = ch.lower()
        if ch_lower in KEYMAP:
            vx_norm, vy_norm, vz_norm, omega_norm = KEYMAP[ch_lower]
            self._vx = vx_norm * self._linear_speed
            self._vy = vy_norm * self._linear_speed
            self._vz = vz_norm * self._linear_speed
            self._omega_z = omega_norm * self._angular_speed
            self._last_key_time = time.monotonic()

    def _publish_current(self) -> None:
        """현 velocity 측 publish. key timeout 측 zero (hover)."""
        elapsed = time.monotonic() - self._last_key_time
        if elapsed > self._key_timeout_s:
            self._vx = 0.0
            self._vy = 0.0
            self._vz = 0.0
            self._omega_z = 0.0

        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._frame_id
        msg.twist.linear.x = self._vx
        msg.twist.linear.y = self._vy
        msg.twist.linear.z = self._vz
        msg.twist.angular.z = self._omega_z
        self._publisher.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = TeleopKeyboardNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        # non-tty 측 `__init__` 측 raise — 사용자 친화 message + exit 1.
        print(f'\n[teleop_keyboard] ERROR: {e}', file=sys.stderr, flush=True)
        rclpy.try_shutdown()
        sys.exit(1)
    finally:
        if node is not None:
            node.restore_terminal()
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
