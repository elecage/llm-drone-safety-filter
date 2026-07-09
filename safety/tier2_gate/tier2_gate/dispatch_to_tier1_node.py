"""A4-3 — `/tier2/cmd/dispatch` JSON → tier1 nominal velocity setpoint.

tier2_gate.gate_node 가 ACCEPT 한 명령 (JSON, ``move_to`` 만 paper-1 1차) 을
받아 P-controller 로 velocity 산출 → ``/cmd/trajectory_setpoint_nominal`` (
geometry_msgs/TwistStamped, ENU) 로 publish. ADR-0011 D1·D2 (tier1 인터페이스 =
nominal velocity 토픽 분리) 정합.

inspect / return_to_dock / emergency_land / ask_user 는 paper §C 후속 — 본
어댑터는 ``move_to`` 외 dispatch 를 warn log 후 무시.
"""

from __future__ import annotations

import json

import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Float32MultiArray, String

from tier2_gate._control import compute_velocity


class DispatchToTier1Node(Node):
    """dispatch JSON → P-controller → TwistStamped publisher."""

    def __init__(self) -> None:
        super().__init__('tier2_dispatch_to_tier1')

        self.declare_parameter('kp', 0.5)
        self.declare_parameter('max_speed_default', 0.3)
        self.declare_parameter('publish_hz', 50.0)
        self.declare_parameter('arrival_threshold', 0.1)

        self._kp = float(self.get_parameter('kp').value)
        self._max_speed_default = float(self.get_parameter('max_speed_default').value)
        self._arrival = float(self.get_parameter('arrival_threshold').value)

        self._goal: tuple[float, float, float] | None = None
        self._goal_max_speed: float = self._max_speed_default
        self._drone_pos: tuple[float, float, float] | None = None

        reliable = QoSProfile(reliability=ReliabilityPolicy.RELIABLE, depth=10)
        sensor_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=10)

        self.create_subscription(
            String, '/tier2/cmd/dispatch', self._on_dispatch, reliable
        )
        self.create_subscription(
            Float32MultiArray, '/tier2/sensor/drone_position_enu',
            self._on_pos, sensor_qos,
        )
        self._pub = self.create_publisher(
            TwistStamped, '/cmd/trajectory_setpoint_nominal', sensor_qos
        )

        hz = float(self.get_parameter('publish_hz').value)
        self.create_timer(1.0 / max(hz, 0.1), self._tick)

        self.get_logger().info(
            f'tier2_dispatch_to_tier1 ready — kp={self._kp} '
            f'max_speed={self._max_speed_default} arrival={self._arrival}'
        )

    def _on_dispatch(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
            sigma = str(payload['sigma'])
            theta = payload.get('theta', {})
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            self.get_logger().warn(f'dispatch payload 무효: {e}')
            return

        if sigma != 'move_to':
            self.get_logger().warn(
                f'A4-3 1차 — {sigma} dispatch 무시 (move_to 만 지원)'
            )
            return

        pos = theta.get('position')
        if not isinstance(pos, (list, tuple)) or len(pos) != 3:
            self.get_logger().warn(
                f'move_to.position 형식 무효: {pos!r}'
            )
            return
        try:
            self._goal = (float(pos[0]), float(pos[1]), float(pos[2]))
            self._goal_max_speed = float(
                theta.get('max_speed', self._max_speed_default)
            )
        except (TypeError, ValueError) as e:
            self.get_logger().warn(f'move_to.theta 형변환 실패: {e}')
            return

        # N1 fix — dispatch 도착했는데 drone_pos 미도착이면 silent
        # 시작 (영원히 publish 안 함) 회피 — 1회 명시적 warn.
        if self._drone_pos is None:
            self.get_logger().warn(
                'move_to ACCEPT 됐으나 drone_pos 미도착 — '
                '/tier2/sensor/drone_position_enu 흐름 확인 필요 '
                '(px4_adapter 다운 또는 PX4 SITL 미실행 가능성).'
            )

    def _on_pos(self, msg: Float32MultiArray) -> None:
        if len(msg.data) >= 3:
            self._drone_pos = (
                float(msg.data[0]), float(msg.data[1]), float(msg.data[2])
            )

    def _tick(self) -> None:
        if self._goal is None or self._drone_pos is None:
            return
        vx, vy, vz = compute_velocity(
            self._goal, self._drone_pos,
            kp=self._kp,
            max_speed=self._goal_max_speed,
            arrival_threshold=self._arrival,
        )
        out = TwistStamped()
        out.header.stamp = self.get_clock().now().to_msg()
        out.header.frame_id = 'enu_local'
        out.twist.linear.x = vx
        out.twist.linear.y = vy
        out.twist.linear.z = vz
        self._pub.publish(out)


def main() -> None:
    rclpy.init()
    node = DispatchToTier1Node()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
