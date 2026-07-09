"""사용자 TF + 회피 영역 RViz 마커 노드.

발행 토픽
---------
- ``/tf_static`` — ``world → user`` static transform (StaticTransformBroadcaster).
- ``/user_avoidance_zone`` — ``visualization_msgs/Marker`` (SPHERE, ``user`` frame
  중심, 지름 = 2 ⋅ r_min). RViz에서 추가하면 반투명 구로 보임.

파라미터 (ROS 2)
----------------
- ``user_x, user_y, user_z`` [float]: 사용자 머리 중심 좌표. 기본 (0.0, -1.0, 1.1)
  — S6/S5/S7 §2.3 (레이아웃 v2, ADR-0009).
- ``r_min`` [float]: 단조성-하한 안전 마진 [m]. 기본 0.7 — S6 §2.4.
- ``parent_frame`` [str]: 부모 frame_id. 기본 ``world``.
- ``user_frame`` [str]: 사용자 frame_id. 기본 ``user``.
- ``marker_period_s`` [float]: 마커 재발행 주기 [s]. 기본 0.5.
- ``marker_color_rgba`` [4 × float]: 마커 RGBA. 기본 (1.0, 0.3, 0.3, 0.3) — 반투명 빨강.
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

from geometry_msgs.msg import TransformStamped
from tf2_ros import StaticTransformBroadcaster
from visualization_msgs.msg import Marker


class UserMarkerNode(Node):
    def __init__(self) -> None:
        super().__init__('user_marker_node')

        # v4.1 layout (2026-05-30): 휠체어가 소파(-1.8, 1.5) 동쪽 옆자리
        # (0, 1.5, 1.1) — 등받이 라인 y=1.5 정렬, TV(-1.8, -1.5) 방향 정면.
        # 기존 v3 (-2.6, 1.5)는 sofa 박스 footprint(x∈[-2.8, -0.8], y∈[1.05, 1.95])
        # 안에 들어가 시각·물리적으로 겹쳤고, 중간 시안 v4 (0, 0) 거실 정중앙은
        # dock(0.5, -0.5, 0.025)과 3D sphere 거리 1.18m로 r_min=0.9 마진이
        # 빠듯해 이륙·hover 불안정 → v4.1로 이동, dock 3D 거리 ≈ 2.29m 안전.
        # livingroom_base.sdf user_avoidance_visual pose 와 동기.
        self.declare_parameter('user_x', 0.0)
        self.declare_parameter('user_y', 1.5)
        self.declare_parameter('user_z', 1.1)
        # r_min = 0.9 m (2026-05-25 갱신, cmsm-proof §7.1 P1):
        # r_drone (0.142) + d_brake (0.025) + b_human (0.75) ≈ 0.917 → 0.9 round.
        # b_human=0.75는 Duncan & Murphy 2013 passing upper bound + 준정적 사용자 보정.
        # Wögerbauer et al. 2024 (Vision 8(4):59) grand mean 1.84m은 upper bound 권장.
        self.declare_parameter('r_min', 0.9)
        self.declare_parameter('parent_frame', 'world')
        self.declare_parameter('user_frame', 'user')
        self.declare_parameter('marker_period_s', 0.5)
        self.declare_parameter('marker_color_rgba', [1.0, 0.3, 0.3, 0.3])

        self._tf_broadcaster = StaticTransformBroadcaster(self)
        self._publish_static_tf()

        marker_qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._marker_pub = self.create_publisher(
            Marker, 'user_avoidance_zone', marker_qos
        )
        self._publish_marker()
        period = float(self.get_parameter('marker_period_s').value)
        self._timer = self.create_timer(period, self._publish_marker)

        self.get_logger().info(
            'user_marker_node up — TF %s→%s at (%.2f, %.2f, %.2f), r_min=%.2f m'
            % (
                self.get_parameter('parent_frame').value,
                self.get_parameter('user_frame').value,
                float(self.get_parameter('user_x').value),
                float(self.get_parameter('user_y').value),
                float(self.get_parameter('user_z').value),
                float(self.get_parameter('r_min').value),
            )
        )

    def _publish_static_tf(self) -> None:
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = str(self.get_parameter('parent_frame').value)
        t.child_frame_id = str(self.get_parameter('user_frame').value)
        t.transform.translation.x = float(self.get_parameter('user_x').value)
        t.transform.translation.y = float(self.get_parameter('user_y').value)
        t.transform.translation.z = float(self.get_parameter('user_z').value)
        t.transform.rotation.w = 1.0
        self._tf_broadcaster.sendTransform(t)

    def _publish_marker(self) -> None:
        m = Marker()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = str(self.get_parameter('user_frame').value)
        m.ns = 'user_avoidance_zone'
        m.id = 0
        m.type = Marker.SPHERE
        m.action = Marker.ADD
        r = float(self.get_parameter('r_min').value)
        m.scale.x = 2.0 * r
        m.scale.y = 2.0 * r
        m.scale.z = 2.0 * r
        rgba = list(self.get_parameter('marker_color_rgba').value)
        m.color.r = float(rgba[0])
        m.color.g = float(rgba[1])
        m.color.b = float(rgba[2])
        m.color.a = float(rgba[3])
        m.pose.orientation.w = 1.0
        self._marker_pub.publish(m)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = UserMarkerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
