#!/usr/bin/env python3
"""A4-2 mock intent publisher — 4 시나리오를 /intent/command 로 발송.

ROS 2 (rclpy) 환경 — Docker 컨테이너에서 ros2 sourced 후 실행:

  $ python3 scripts/mock_tier2_intent.py --scenario accept
  $ python3 scripts/mock_tier2_intent.py --scenario reject_cc1
  $ python3 scripts/mock_tier2_intent.py --scenario reject_phi4
  $ python3 scripts/mock_tier2_intent.py --scenario confirm_phi10

별도 터미널에서 ``ros2 topic echo /tier2/gate/decision`` 로 결과 확인.

A4-3 sim 통합 시 ``scripts/check_tier2_smoke.sh`` 가 본 스크립트를 호출해 4
시나리오 자동 검증.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


# 모든 시나리오의 move_to position 을 동일 [1, 1, 1] 로 통일 — accept scenario
# 후 confirm_phi10 #1 이 C1 (위치 변경 모순) 에 우발 걸리지 않도록 (σ_prev 가
# 누적되는 사이 순서를 가정). confirm_phi10 #2 의 return_to_dock 이 C2 로
# Φ_10 confirm 을 명시적으로 발동.
_MOVE_TO_DEFAULT = {'position': [1.0, 1.0, 1.0], 'max_speed': 0.3}

SCENARIOS = {
    'accept': [
        {'sigma': 'move_to', 'theta': _MOVE_TO_DEFAULT, 'c': 0.9},
    ],
    'reject_cc1': [
        {'sigma': 'teleport', 'theta': {}, 'c': 0.9},
    ],
    'reject_phi4': [
        {'sigma': 'move_to', 'theta': _MOVE_TO_DEFAULT, 'c': 0.2},
    ],
    'confirm_phi10': [
        # 1차 ACCEPT (σ_prev = move_to[1,1,1]) → 2차 return_to_dock = C2 contradicts → confirm.
        {'sigma': 'move_to', 'theta': _MOVE_TO_DEFAULT, 'c': 0.9},
        {'sigma': 'return_to_dock', 'theta': {}, 'c': 0.9},
    ],
}


class MockIntentPublisher(Node):
    def __init__(self, scenario: str, delay_s: float = 0.5) -> None:
        super().__init__('mock_intent_publisher')
        qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE, depth=10)
        self._pub = self.create_publisher(String, '/intent/command', qos)
        self._scenario = scenario
        self._delay = delay_s
        self.get_logger().info(f'scenario={scenario}, will publish in {delay_s}s')

    def publish_all(self) -> None:
        time.sleep(self._delay)
        for i, payload in enumerate(SCENARIOS[self._scenario]):
            msg = String(data=json.dumps(payload))
            self._pub.publish(msg)
            self.get_logger().info(f'[{i+1}/{len(SCENARIOS[self._scenario])}] '
                                   f'published: {payload}')
            time.sleep(self._delay)


def main() -> None:
    parser = argparse.ArgumentParser(description='A4-2 mock intent publisher')
    parser.add_argument('--scenario', choices=sorted(SCENARIOS.keys()),
                        required=True, help='4 결정 분기 중 하나')
    parser.add_argument('--delay', type=float, default=0.5,
                        help='발송 전 + 발송 간 sleep [s]')
    args = parser.parse_args()

    rclpy.init()
    node = MockIntentPublisher(args.scenario, args.delay)
    try:
        node.publish_all()
        # 결정 토픽 응답 대기
        time.sleep(args.delay)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    sys.exit(main() or 0)
