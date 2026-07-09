#!/usr/bin/env python3
"""gz_cam_relay_node.py — host 카메라 중계 수신 → /camera/image_raw 발행 (컨테이너 측 절반).

host 측 절반 = scripts/gz_cam_relay_host.py (프로토콜·배경 설명 그쪽 머리주석).
컨테이너에서 host.docker.internal:<port> 로 *나가는* TCP 연결만 사용 —
macOS Docker Desktop 에서 검증된 통신 방향 (Ollama 와 동일 패턴).

발행: /camera/image_raw (sensor_msgs/Image, SENSOR_DATA QoS)
    — intent_ovd detector_node 의 기본 구독 토픽·QoS 와 정합 (B1).
타임스탬프: host gz sim time 을 그대로 싣는다 (frame 단위 정합).

실행 (컨테이너 안):
    python3 /workspace/scripts/gz_cam_relay_node.py \
        --ros-args -p relay_host:=host.docker.internal -p relay_port:=15601
"""

from __future__ import annotations

import socket
import struct
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

MAGIC = 0x675A4631  # 'gZF1' — gz_cam_relay_host.py 와 동기.
_HEADER = struct.Struct("!II")          # magic, payload_len
_META = struct.Struct("!IIIII")         # width, height, step, sec, nsec


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("relay 연결 종료")
        buf.extend(chunk)
    return bytes(buf)


class GzCamRelayNode(Node):
    def __init__(self) -> None:
        super().__init__("gz_cam_relay")
        self.declare_parameter("relay_host", "host.docker.internal")
        self.declare_parameter("relay_port", 15601)
        self.declare_parameter("output_topic", "/camera/image_raw")
        self.declare_parameter("frame_id", "front_camera")
        self.declare_parameter("reconnect_period_s", 2.0)

        self._relay_host = str(self.get_parameter("relay_host").value)
        self._relay_port = int(self.get_parameter("relay_port").value)
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._reconnect_s = float(self.get_parameter("reconnect_period_s").value)
        out_topic = str(self.get_parameter("output_topic").value)

        self._pub = self.create_publisher(Image, out_topic, qos_profile_sensor_data)
        self._stop = threading.Event()
        self._rx_count = 0
        self._thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._thread.start()
        self.get_logger().info(
            f"gz_cam_relay 시작 — {self._relay_host}:{self._relay_port} → {out_topic}"
        )

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection(
                    (self._relay_host, self._relay_port), timeout=5.0
                ) as sock:
                    sock.settimeout(10.0)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.get_logger().info("relay 연결 성공 — 프레임 수신 시작")
                    while not self._stop.is_set():
                        self._rx_one(sock)
            except (OSError, ConnectionError) as exc:
                if self._stop.is_set():
                    return
                self.get_logger().warning(
                    f"relay 연결 실패/끊김 ({exc}) — {self._reconnect_s:.0f}s 후 재시도"
                )
                self._stop.wait(self._reconnect_s)

    def _rx_one(self, sock: socket.socket) -> None:
        magic, payload_len = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
        if magic != MAGIC:
            raise ConnectionError(f"프로토콜 magic 불일치: {magic:#x}")
        payload = _recv_exact(sock, payload_len)
        width, height, step, sec, nsec = _META.unpack_from(payload, 0)
        off = _META.size
        (fmt_len,) = struct.unpack_from("!I", payload, off)
        off += 4
        encoding = payload[off:off + fmt_len].decode("ascii")
        off += fmt_len
        data = payload[off:]

        msg = Image()
        msg.header.stamp.sec = sec
        msg.header.stamp.nanosec = nsec
        msg.header.frame_id = self._frame_id
        msg.width = width
        msg.height = height
        msg.step = step
        msg.encoding = encoding
        msg.is_bigendian = 0
        msg.data = data
        self._pub.publish(msg)
        self._rx_count += 1
        if self._rx_count % 100 == 1:
            self.get_logger().info(f"프레임 발행 누적 {self._rx_count}")

    def destroy_node(self):  # noqa: D102 — rclpy 오버라이드.
        self._stop.set()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = GzCamRelayNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
