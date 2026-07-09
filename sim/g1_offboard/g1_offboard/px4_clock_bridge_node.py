"""PX4 → ROS 2 /clock 브릿지 노드 (Task #5).

ADR-0008 α' 토폴로지(PX4·gz = macOS native, ROS 2 = Docker 컨테이너)에서
Gazebo의 /clock 토픽이 컨테이너로 전달되지 않는다. uXRCE-DDS는 /fmu/* 만
bridge하고 /clock 은 아니다 (ADR-0011 D4 amendment 원인).

이 노드는 /fmu/out/vehicle_local_position_v1 (~50 Hz, uXRCE-DDS로 이미 bridge됨)
의 timestamp 필드를 rosgraph_msgs/msg/Clock 으로 변환해 /clock 에 publish한다.
이로써 다른 모든 노드가 use_sim_time=True 로 PX4 SITL 시뮬레이션 시간을 기준으로
동작할 수 있다. 본 노드 자체는 /clock 의 생산자이므로 use_sim_time=False (wall time)
로 launch 파일에서 설정해야 한다.

발행 주기: /fmu/out/vehicle_local_position_v1 콜백 기반 (~50 Hz).
timestamp 단위: PX4 hrt [μs], SITL 에서 Gazebo sim time 과 동기.
"""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

from px4_msgs.msg import VehicleLocalPosition
from rosgraph_msgs.msg import Clock


def _px4_qos() -> QoSProfile:
    """PX4 uXRCE-DDS 호환 QoS (BEST_EFFORT + VOLATILE + KEEP_LAST 5)."""
    return QoSProfile(
        reliability=QoSReliabilityPolicy.BEST_EFFORT,
        durability=QoSDurabilityPolicy.VOLATILE,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=5,
    )


class Px4ClockBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__('px4_clock_bridge')

        self._pub_clock = self.create_publisher(Clock, '/clock', 10)
        self._sub_local_pos = self.create_subscription(
            VehicleLocalPosition,
            '/fmu/out/vehicle_local_position_v1',
            self._on_local_pos,
            _px4_qos(),
        )
        self.get_logger().info(
            'px4_clock_bridge 시작 — /fmu/out/vehicle_local_position_v1 → /clock'
        )

    def _on_local_pos(self, msg: VehicleLocalPosition) -> None:
        ts_us: int = msg.timestamp  # μs since PX4 boot (= Gazebo sim time in SITL)
        clock_msg = Clock()
        clock_msg.clock.sec = int(ts_us // 1_000_000)
        clock_msg.clock.nanosec = int((ts_us % 1_000_000) * 1000)
        self._pub_clock.publish(clock_msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = Px4ClockBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
