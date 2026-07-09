"""intent_context context_graph_publisher — scenario 장면 + 드론 위치 발행 (ROS 2).

[ROADMAP C36](../../../docs/handover/ROADMAP.md) #2 — B3/B4 baseline 의 context
augmentation. scenario 파라미터 → 정적 장면 + (옵션) 드론 현재 위치를
`/intent/context_graph` (std_msgs/String JSON) 로 주기 발행. wrapper_node(fusion
mode) 가 구독해 LLM 입력 주입.

## 책임 분리 (pure / ROS 2)

| 모듈 | 내용 | 검증 |
|---|---|---|
| `context_graph.py` | scenario → 장면 dict 조립 + 직렬화 | ✅ host venv |
| `context_graph_publisher.py` (본 모듈) | rclpy 노드 — timer 주기 발행 + PX4 위치 구독 | ⚠️ colcon (Mac mini) |

## 파라미터

- `scenario` (str, 필수) — scenario_id ('S5'-'S8'). launch_composition 측 전달.
- `output_topic` (str, `/intent/context_graph`) — wrapper_node default 정합.
- `publish_hz` (float, 2.0) — 정적 내용이지만 late-joiner(wrapper) 측 수신 보장
  위해 주기 발행 + 드론 위치 갱신 반영.

## 드론 위치 주입 (순수 방향 명령 해석 지원)

`/fmu/out/vehicle_local_position_v1` 구독 → NED→ENU 변환 + scenario spawn offset
더해 world ENU 좌표 산출 → context graph 의 `drone_position` 필드로 포함. LLM 이
"left", "forward" 같은 순수 방향 명령 받았을 때 *드론 현재 위치 기준* 으로 절대
좌표를 계산하는 데 사용 ([_llm_prompt.py](../../../intent/llm/intent_llm/_llm_prompt.py)
SYSTEM_PROMPT 참조). PX4 위치 미수신 시 `drone_position` 필드 누락 → LLM 은
SYSTEM_PROMPT 의 *기준점 없으면 ask_user* 분기로 fallback.
"""

from __future__ import annotations

import sys

import rclpy
from px4_msgs.msg import VehicleLocalPosition
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from intent_context.context_graph import (
    build_context_graph,
    serialize_context_graph,
)


def _px4_qos(depth: int = 10) -> QoSProfile:
    """PX4 micro-XRCE-DDS 토픽 QoS — BEST_EFFORT, VOLATILE, KEEP_LAST."""
    return QoSProfile(
        reliability=QoSReliabilityPolicy.BEST_EFFORT,
        durability=QoSDurabilityPolicy.VOLATILE,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=depth,
    )


class ContextGraphPublisherNode(Node):
    """scenario 장면 context graph + 드론 위치 주기 발행 노드."""

    def __init__(self) -> None:
        super().__init__('intent_context_graph')

        self.declare_parameter('scenario', '')
        self.declare_parameter('output_topic', '/intent/context_graph')
        self.declare_parameter('publish_hz', 2.0)

        scenario = str(self.get_parameter('scenario').value)
        output_topic = str(self.get_parameter('output_topic').value)
        publish_hz = float(self.get_parameter('publish_hz').value)

        if not scenario.strip():
            raise ValueError('scenario 파라미터 필수 — 빈 문자열 불가')
        if publish_hz <= 0.0:
            raise ValueError(f'publish_hz={publish_hz} 무효 — 양의 실수 필수')

        self._scenario = scenario

        # spawn offset (scenario_params single source) — sigma_bridge 와 동일 변환.
        self._spawn_x, self._spawn_y, self._spawn_z = self._resolve_spawn(scenario)

        # 드론 world ENU 위치 — PX4 수신 시 갱신, 미수신 시 None.
        self._drone_world_xyz: tuple[float, float, float] | None = None

        self._pub = self.create_publisher(String, output_topic, 10)
        self.create_subscription(
            VehicleLocalPosition,
            '/fmu/out/vehicle_local_position_v1',
            self._on_local_pos,
            _px4_qos(),
        )
        self.create_timer(1.0 / publish_hz, self._on_timer)
        self._on_timer()  # 즉시 1회 발행 (드론 위치 없음 → 정적 graph 만)

        # 정적 부분 로그 1회.
        try:
            static_graph = build_context_graph(scenario)
            objects_n = len(static_graph['objects'])
        except Exception:  # noqa: BLE001 — 로그용
            objects_n = -1
        self.get_logger().info(
            f'context_graph_publisher ready — scenario={scenario} '
            f'objects={objects_n} spawn=({self._spawn_x},{self._spawn_y},{self._spawn_z}) '
            f'→ {output_topic} @ {publish_hz} Hz (drone_position 동적 주입)'
        )

    def _resolve_spawn(self, scenario_id: str) -> tuple[float, float, float]:
        """scenario_id → spawn world ENU (scenario_params). 실패 시 (0,0,0)."""
        try:
            from scenario_params.params import scenario_location, spawn_params
            location = scenario_location(scenario_id)
            sp = spawn_params(location)
            return sp['spawn_x'], sp['spawn_y'], sp['spawn_z']
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(
                f'spawn lookup 실패 (scenario={scenario_id!r}) → spawn=(0,0,0) '
                f'보정 없음: {exc}'
            )
            return 0.0, 0.0, 0.0

    def _on_local_pos(self, msg: VehicleLocalPosition) -> None:
        # NED → ENU + spawn offset = world ENU
        # NED→ENU: x_ENU = y_NED, y_ENU = x_NED, z_ENU = -z_NED
        enu_x = float(msg.y)
        enu_y = float(msg.x)
        enu_z = float(-msg.z)
        self._drone_world_xyz = (
            enu_x + self._spawn_x,
            enu_y + self._spawn_y,
            enu_z + self._spawn_z,
        )

    def _on_timer(self) -> None:
        drone_pos = list(self._drone_world_xyz) if self._drone_world_xyz else None
        graph = build_context_graph(self._scenario, drone_world_position=drone_pos)
        self._pub.publish(String(data=serialize_context_graph(graph)))


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = ContextGraphPublisherNode()
    except Exception as exc:  # noqa: BLE001 — init 실패 명확 보고 후 재raise
        print(f'[context_graph_publisher] init 실패: {exc}', file=sys.stderr)
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
