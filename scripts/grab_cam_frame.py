#!/usr/bin/env python3
"""드론 전방 카메라(/camera/image_raw) 단일 프레임 캡처 → PNG 저장.

영속 셸(up.sh DRONE_CAMERA=1)이 gz 카메라를 /camera/image_raw 로 중계 중일 때,
한 프레임을 받아 /workspace/results/cam_frame.png 로 저장한다(호스트 results/).
OVD 입력이 실제로 무엇을 보는지 육안 검증 + "발견 C"(대상 FOV 이탈) 점검용.

컨테이너에서:
  docker exec llmdrone-sim bash -c \
    'source /opt/ros/humble/setup.bash && source /workspace/install/setup.bash && \
     python3 /workspace/scripts/grab_cam_frame.py'
"""
from __future__ import annotations

import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

OUT = '/workspace/results/cam_frame.png'
TOPIC = '/camera/image_raw'
TIMEOUT_S = 20.0


class Grab(Node):
    def __init__(self) -> None:
        super().__init__('cam_grab')
        self.saved = False
        self.create_subscription(Image, TOPIC, self._cb, qos_profile_sensor_data)

    def _cb(self, msg: Image) -> None:
        if self.saved:
            return
        from cv_bridge import CvBridge
        import cv2
        img = CvBridge().imgmsg_to_cv2(msg, desired_encoding='bgr8')
        cv2.imwrite(OUT, img)
        self.saved = True
        self.get_logger().info(
            f'saved {msg.width}x{msg.height} enc={msg.encoding} -> {OUT}'
        )


def main() -> int:
    rclpy.init()
    node = Grab()
    t0 = time.time()
    while rclpy.ok() and not node.saved and (time.time() - t0) < TIMEOUT_S:
        rclpy.spin_once(node, timeout_sec=0.5)
    ok = node.saved
    node.destroy_node()
    rclpy.shutdown()
    if not ok:
        sys.stderr.write(f'ERROR: {TIMEOUT_S}s 내 {TOPIC} 프레임 미수신\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
