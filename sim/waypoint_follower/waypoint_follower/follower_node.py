"""follower_node — 목표 지점(pose waypoint) → 연속 속도 공칭 입력 (ROS 2).

[ADR-0029](../../../docs/handover/decisions/0029-trial-integration-live-path.md)
블로커 3 — *의도-제어 변환* 의 modality 정합. sigma_bridge 가 내는 1회성 pose
waypoint 를 *연속 속도* `/cmd/trajectory_setpoint_nominal` (TwistStamped, ENU) 로
20 Hz 스트림 → tier1 의 *정형 보증된* 속도 CBF-QP 가 매 tick 필터 → safe twist →
g1. 목표 도달 시 속도가 0 으로 수렴(bounded)하므로 세션 44 S6 의 멈추지 않는
등속도 접선 탈주가 없다.

## 토픽 계약

| 방향 | 파라미터 (default) | 타입 | 내용 |
|---|---|---|---|
| 구독 | `waypoint_topic` (`/intent/target_waypoint`) | PoseStamped | 목표 지점 (ENU world) |
| 구독 | `vlp_topic` (`/fmu/out/vehicle_local_position_v1`) | VehicleLocalPosition | 드론 위치 (NED) |
| 발행 | `output_topic` (`/cmd/trajectory_setpoint_nominal`) | TwistStamped | 공칭 속도 (ENU) |

순수 로직(`waypoint_velocity` · `ned_to_enu_position`)은 [waypoint_velocity](waypoint_velocity.py)
모듈에 분리(host venv 단위 테스트). 본 노드는 rclpy wiring 만 — colcon test(Docker)·
Mac mini e2e 에서 검증.
"""

from __future__ import annotations

import math
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from geometry_msgs.msg import PoseStamped, TwistStamped

from waypoint_follower.waypoint_velocity import (
    ned_to_enu_position,
    quaternion_zw_to_yaw,
    waypoint_velocity,
    yaw_rate_to_target,
)


class FollowerNode(Node):
    """pose waypoint + 드론 위치 → 연속 속도 공칭 입력 발행 노드."""

    def __init__(self) -> None:
        super().__init__('waypoint_follower')

        self.declare_parameter('waypoint_topic', '/intent/target_waypoint')
        self.declare_parameter('output_topic', '/cmd/trajectory_setpoint_nominal')
        self.declare_parameter('vlp_topic', '/fmu/out/vehicle_local_position_v1')
        self.declare_parameter('k_p', 0.6)            # [1/s] 거리→속도 비례 게인
        self.declare_parameter('u_max', 0.5)          # [m/s] paper §5 u_max 정합
        self.declare_parameter('publish_rate_hz', 20.0)
        self.declare_parameter('arrival_radius_m', 0.15)
        # yaw 추종 (ADR-0031 inspect vantage) — 목표 자세(orientation)가 주어지면
        # 전방 고정 카메라가 대상을 향하도록 yawspeed 를 함께 낸다. 목표 yaw 없는
        # (all-zero quaternion) 일반 이동에선 yawspeed=0 (기존 동작 보존).
        self.declare_parameter('k_yaw', 1.2)          # [1/s] yaw 오차→yawspeed 게인
        self.declare_parameter('yawrate_max', 0.8)    # [rad/s] yawspeed 상한

        self.k_p = float(self.get_parameter('k_p').value)
        self.u_max = float(self.get_parameter('u_max').value)
        self.arrival_radius_m = float(self.get_parameter('arrival_radius_m').value)
        self.k_yaw = float(self.get_parameter('k_yaw').value)
        self.yawrate_max = float(self.get_parameter('yawrate_max').value)
        rate_hz = float(self.get_parameter('publish_rate_hz').value)
        if rate_hz <= 0.0:
            raise ValueError(f'publish_rate_hz 는 양수: {rate_hz}')
        waypoint_topic = str(self.get_parameter('waypoint_topic').value)
        output_topic = str(self.get_parameter('output_topic').value)
        vlp_topic = str(self.get_parameter('vlp_topic').value)

        # px4_msgs 는 live 경로에서만 import (host 단위 테스트가 의존하지 않도록).
        from px4_msgs.msg import VehicleLocalPosition

        self._target_enu = None      # (x, y, z) ENU world — 미수신이면 None.
        self._target_yaw = None      # 목표 yaw [rad] ENU — None=yaw 제어 안 함.
        self._pos_enu = None         # (x, y, z) ENU world (VLP NED→ENU).
        self._yaw_enu = None         # 현재 드론 yaw [rad] ENU — None=미수신.

        self._pub = self.create_publisher(TwistStamped, output_topic, 10)
        self.create_subscription(
            PoseStamped, waypoint_topic, self._on_waypoint, 10,
        )
        self.create_subscription(
            VehicleLocalPosition, vlp_topic, self._on_vlp, qos_profile_sensor_data,
        )
        self._timer = self.create_timer(1.0 / rate_hz, self._on_timer)

        self.get_logger().info(
            f'waypoint_follower 시작 — k_p={self.k_p}, u_max={self.u_max}, '
            f'rate={rate_hz}Hz, arrival_r={self.arrival_radius_m}m, '
            f'wp={waypoint_topic}, vlp={vlp_topic}, out={output_topic}'
        )

    def _on_waypoint(self, msg: PoseStamped) -> None:
        p = msg.pose.position
        self._target_enu = (float(p.x), float(p.y), float(p.z))
        # 목표 자세 — yaw 만 인코딩(x=y=0). all-zero quaternion 이면 yaw 의도
        # 없음(None) → 일반 이동(기존 동작). vantage(ADR-0031)는 yaw 를 채워 보냄.
        o = msg.pose.orientation
        self._target_yaw = quaternion_zw_to_yaw(float(o.z), float(o.w))

    def _on_vlp(self, msg) -> None:
        self._pos_enu = ned_to_enu_position(float(msg.x), float(msg.y), float(msg.z))
        # PX4 heading 은 NED yaw(North 기준 CW). ENU yaw(East 기준 CCW) = π/2 − heading
        # (g1_offboard.frame_conversions 규약 정합).
        self._yaw_enu = (math.pi / 2.0) - float(msg.heading)

    def _on_timer(self) -> None:
        # 목표·위치 둘 다 있어야 공칭 발행 (미수신 시 무발행 → tier1 입력 없음).
        if self._target_enu is None or self._pos_enu is None:
            return
        vx, vy, vz = waypoint_velocity(
            self._target_enu, self._pos_enu, self.k_p, self.u_max,
            self.arrival_radius_m,
        )
        # yaw 추종 — 목표 yaw 와 현재 yaw 가 모두 있을 때만(vantage). 없으면 0
        # (일반 이동·heading 미수신). twist.angular.z 는 tier1(yawspeed pass-through)
        # 을 거쳐 g1 yawspeed 로 전달 — 위치 속도 CBF 와 직교.
        yaw_rate = 0.0
        if self._target_yaw is not None and self._yaw_enu is not None:
            yaw_rate = yaw_rate_to_target(
                self._target_yaw, self._yaw_enu, self.k_yaw, self.yawrate_max,
            )
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'world'
        msg.twist.linear.x = vx
        msg.twist.linear.y = vy
        msg.twist.linear.z = vz
        msg.twist.angular.z = yaw_rate
        self._pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = FollowerNode()
    except Exception as exc:  # noqa: BLE001 — init 실패 명확 보고 후 재raise
        print(f'[waypoint_follower] init 실패: {exc}', file=sys.stderr)
        rclpy.shutdown()
        raise
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
