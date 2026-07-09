"""A4-3 — PX4 raw 토픽 → tier2_gate std_msgs 어댑터.

tier2_gate.gate_node 가 std_msgs only 로 testability 를 유지 (A4-2 결정) —
PX4 의존 (px4_msgs) 은 본 어댑터 노드에만 격리.

토픽 매핑 (PX4 main message versioning ``_v1`` suffix 정합):

  ``/fmu/out/vehicle_local_position_v1`` (px4_msgs/VehicleLocalPosition, NED)
    → ``/tier2/sensor/drone_position_enu`` (std_msgs/Float32MultiArray, ENU 3 floats)

  ``/fmu/out/battery_status_v1`` (px4_msgs/BatteryStatus, remaining ∈ [0, 1])
    → ``/tier2/sensor/battery_pct`` (std_msgs/Float32, 0~100)

NED↔ENU 변환은 [_frames.py](_frames.py) — ADR-0011 D3 사상 적용. 변환 사상은
involution 이라 NED→ENU 와 ENU→NED 가 같은 함수.
"""

from __future__ import annotations

import rclpy
from px4_msgs.msg import BatteryStatus, VehicleLocalPosition
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Float32, Float32MultiArray

from tier2_gate._frames import battery_remaining_to_pct, ned_to_enu


class Px4AdapterNode(Node):
    """PX4 → tier2_gate 어댑터 — 두 토픽 매핑."""

    def __init__(self) -> None:
        super().__init__('tier2_px4_adapter')

        # PX4 publisher 의 QoS = BEST_EFFORT + TRANSIENT_LOCAL (sim trace F-smoke 검증).
        px4_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=10)
        out_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=10)

        # ---- Subscribers (PX4 main _v1 versioning) ----
        self.create_subscription(
            VehicleLocalPosition, '/fmu/out/vehicle_local_position_v1',
            self._on_local_pos, px4_qos,
        )
        self.create_subscription(
            BatteryStatus, '/fmu/out/battery_status_v1',
            self._on_battery, px4_qos,
        )

        # ---- Publishers ----
        self._pub_pos = self.create_publisher(
            Float32MultiArray, '/tier2/sensor/drone_position_enu', out_qos
        )
        self._pub_battery = self.create_publisher(
            Float32, '/tier2/sensor/battery_pct', out_qos
        )

        self.get_logger().info(
            'tier2_px4_adapter ready — VehicleLocalPosition→ENU + BatteryStatus→pct'
        )

    def _on_local_pos(self, msg: VehicleLocalPosition) -> None:
        """NED (msg.x, msg.y, msg.z) → ENU (y, x, -z), Float32MultiArray publish.

        N3 fix — PX4 EKF 가 추정 실패 (`xy_valid` / `z_valid` flag = False) 시
        msg.x/y/z 는 stale 또는 garbage. 잘못된 drone_pos 가 tier2_gate 의
        in-progress 판정·CBF 계산을 오염시키지 않도록 publish skip + warn throttle.
        """
        if not msg.xy_valid or not msg.z_valid:
            self.get_logger().warn(
                f'EKF estimate invalid (xy_valid={msg.xy_valid} '
                f'z_valid={msg.z_valid}) — drone_pos publish skip.',
                throttle_duration_sec=5.0,
            )
            return
        x_enu, y_enu, z_enu = ned_to_enu(
            float(msg.x), float(msg.y), float(msg.z)
        )
        out = Float32MultiArray()
        out.data = [x_enu, y_enu, z_enu]
        self._pub_pos.publish(out)

    def _on_battery(self, msg: BatteryStatus) -> None:
        """BatteryStatus.remaining (0~1) → 백분율 (0~100), clamp."""
        pct = battery_remaining_to_pct(float(msg.remaining))
        self._pub_battery.publish(Float32(data=pct))


def main() -> None:
    rclpy.init()
    node = Px4AdapterNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
