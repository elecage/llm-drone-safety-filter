#!/usr/bin/env python3
"""gz_cam_relay_host.py — host gz 카메라 → Docker 컨테이너 TCP 중계 (host 측 절반).

배경 (P1 OVD 입력 사슬, ADR-0024 정합):
    macOS native 트랙(ADR-0008)에서 gz 서버는 host 에서 돌고 ROS 2 는 Docker
    컨테이너 안에서 돈다. ros_gz bridge 로 직접 잇는 표준 경로는 macOS Docker
    Desktop 에서 차단됨을 실측으로 확인 (2026-06-11):
      (1) gz-transport discovery 멀티캐스트가 컨테이너 경계를 못 건넘,
      (2) GZ_RELAY unicast 우회도 컨테이너 측 discovery 소켓이 멀티캐스트
          주소(239.255.0.7)에 bind 되어 unicast 송신이 EPERM,
      (3) host 발행자의 advertise 주소가 GZ_IP=127.0.0.1 (PX4 make target
          하드코딩) 이라 컨테이너에서 도달 불가.
    따라서 검증된 원시 경로만 사용한다: host 측 gz Python 바인딩 구독
    (Homebrew gz-transport13) + 컨테이너 → host TCP (host.docker.internal —
    Ollama 와 동일 패턴). Linux 단일 호스트(CI 등)에선 본 중계 없이 ros_gz
    bridge 직결로 대체 가능 — 운용 다리이지 아키텍처 구성요소가 아니다.

프로토콜 (컨테이너 측 절반 = scripts/gz_cam_relay_node.py 와 동기):
    클라이언트(컨테이너)가 connect → 서버(본 스크립트)가 프레임 스트림 송신.
    프레임 = MAGIC(4B '!I') + payload_len(4B '!I') + payload.
    payload = struct '!IIIII' (width, height, step, sec, nsec)
              + fmt_len(4B '!I') + fmt(ascii — ROS encoding 문자열)
              + data (raw bytes).
    최신 프레임 우선(latest-wins) — 송신이 밀리면 중간 프레임은 버린다.

실행 (Mac mini host, sim 가동 후):
    python3 scripts/gz_cam_relay_host.py \
        --topic /drone/front_camera/image --port 15601

주의: gz Python 바인딩(gz.transport13/gz.msgs10)은 Homebrew gz-harmonic 이
    설치한 site-packages 에 있고 protobuf 파이썬 패키지를 추가로 요구한다.
    1회 준비 (Mac mini host):
        /opt/homebrew/bin/python3 -m venv --system-site-packages ~/.venvs/llmdrone-gz
        ~/.venvs/llmdrone-gz/bin/pip install protobuf
    실행은 그 venv 의 python 으로 (up.sh 는 GZ_RELAY_PY env 로 받음 —
    ADR-0004 venv 정책의 host 측 sim 운용 변형: gz 바인딩이 brew python
    site-packages 에 있어 리포 .venv 와 분리). GZ_IP=127.0.0.1 export 필요
    (PX4 가 서버 측에 고정 — 미설정 시 discovery 실패).
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import threading
import time

MAGIC = 0x675A4631  # 'gZF1'

# gz.msgs10 PixelFormatType → ROS sensor_msgs encoding 문자열.
_GZ_FMT_TO_ROS = {
    3: "rgb8",     # RGB_INT8
    4: "rgba8",    # RGBA_INT8
    6: "bgr8",     # BGR_INT8
    1: "mono8",    # L_INT8
    2: "mono16",   # L_INT16
}
_BYTES_PER_PIXEL = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "mono8": 1, "mono16": 2}


class _LatestFrame:
    """스레드 안전 latest-wins 프레임 보관함."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._payload: bytes | None = None
        self._seq = 0

    def put(self, payload: bytes) -> None:
        with self._cond:
            self._payload = payload
            self._seq += 1
            self._cond.notify_all()

    def wait_next(self, last_seq: int, timeout: float = 1.0):
        with self._cond:
            if self._seq == last_seq:
                self._cond.wait(timeout)
            if self._seq == last_seq or self._payload is None:
                return None, last_seq
            return self._payload, self._seq


def _build_payload(msg) -> bytes | None:
    fmt = _GZ_FMT_TO_ROS.get(int(msg.pixel_format_type))
    if fmt is None:
        return None
    width = int(msg.width)
    height = int(msg.height)
    step = int(getattr(msg, "step", 0)) or width * _BYTES_PER_PIXEL[fmt]
    sec = int(msg.header.stamp.sec)
    nsec = int(msg.header.stamp.nsec)
    fmt_b = fmt.encode("ascii")
    return (
        struct.pack("!IIIII", width, height, step, sec, nsec)
        + struct.pack("!I", len(fmt_b))
        + fmt_b
        + msg.data
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topic", default="/drone/front_camera/image")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="TCP bind 주소. 컨테이너는 host.docker.internal 로 접속.")
    parser.add_argument("--port", type=int, default=15601)
    args = parser.parse_args()

    try:
        from gz.transport13 import Node  # type: ignore
        from gz.msgs10.image_pb2 import Image  # type: ignore
    except ImportError as exc:
        print(f"[gz_cam_relay_host] ERROR: gz Python 바인딩 import 실패 — {exc}\n"
              "  Homebrew gz-harmonic 의 python3 로 실행하세요 "
              "(예: /opt/homebrew/bin/python3).", file=sys.stderr)
        return 1

    latest = _LatestFrame()
    stats = {"rx": 0, "drop_fmt": 0}

    def _on_image(msg: Image) -> None:
        payload = _build_payload(msg)
        if payload is None:
            stats["drop_fmt"] += 1
            return
        stats["rx"] += 1
        latest.put(payload)

    node = Node()
    if not node.subscribe(Image, args.topic, _on_image):
        print(f"[gz_cam_relay_host] ERROR: 구독 실패 — {args.topic}", file=sys.stderr)
        return 1
    print(f"[gz_cam_relay_host] 구독 {args.topic} → tcp {args.bind}:{args.port}")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind, args.port))
    server.listen(1)

    while True:
        conn, addr = server.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"[gz_cam_relay_host] 클라이언트 연결 {addr} (rx 누적 {stats['rx']})")
        seq = 0
        sent = 0
        try:
            while True:
                payload, seq = latest.wait_next(seq)
                if payload is None:
                    continue
                conn.sendall(struct.pack("!II", MAGIC, len(payload)) + payload)
                sent += 1
                if sent % 100 == 1:
                    print(f"[gz_cam_relay_host] 송신 {sent} 프레임 "
                          f"(rx {stats['rx']}, fmt drop {stats['drop_fmt']})")
        except (BrokenPipeError, ConnectionResetError, OSError):
            print("[gz_cam_relay_host] 클라이언트 연결 끊김 — accept 재대기")
        finally:
            conn.close()
            time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main())
