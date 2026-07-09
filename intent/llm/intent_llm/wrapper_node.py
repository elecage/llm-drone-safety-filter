"""intent_llm wrapper_node — utterance → 의도해석기 → TypedAction publish (ROS 2).

[ROADMAP C36](../../../docs/handover/ROADMAP.md) — runner.py 실행 셸 차단 해소의
첫 벽돌. 텍스트 발화를 구독 → registry 측 backbone wrapper.process() → IntentResult
를 std_msgs/String JSON 으로 발행.

## 책임 분리 (pure / ROS 2)

| 모듈 | 내용 | 검증 |
|---|---|---|
| `wrapper_payload.py` | IntentInput 빌드 + IntentResult 직렬화 + context 파싱 | ✅ host venv |
| `wrapper_node.py` (본 모듈) | rclpy 노드 — 구독/발행/파라미터 | ⚠️ colcon (Mac mini) |

## 토픽 계약

| 방향 | 파라미터 (default) | 타입 | 내용 |
|---|---|---|---|
| 구독 | `utterance_topic` (`/intent/user_prompt_raw`) | std_msgs/String | 발화 텍스트 |
| 구독(fusion) | `context_graph_topic` (`/intent/context_graph`) | std_msgs/String | context graph JSON |
| 발행 | `output_topic` (`/intent/llm_sigma_raw`) | std_msgs/String | {sigma, theta, c, signals} JSON |

토픽은 모두 declare_parameter 로 노출 — launch/runner 측 baseline 별 remap 가능
(예: adversarial fault 활성 시 utterance_topic := `/intent/user_prompt_faulted`).

## 파라미터

- `backbone` (str, 필수) — registry 식별자 (9종, list_registered). 미등록 측 KeyError.
- `scenario` (str, 필수) — scenario_id (IntentInput).
- `mode` (str, 'direct'|'fusion') — fusion 측 context_graph 구독·주입.

> ⚠️ launch_composition.py 측 현재 `backbone='placeholder'` — backbone 이 격자
> 차원이 아니므로(ADR-0014 D5 정합 미결) 실 trial 실행 전 backbone wiring 결정 필요.
"""

from __future__ import annotations

import sys

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from intent_llm.registry import get_wrapper, list_registered
from intent_llm.wrapper_payload import (
    build_intent_input,
    parse_context_graph,
    serialize_result_with_context,
)


_VALID_MODES = ('direct', 'fusion')


class WrapperNode(Node):
    """utterance → 의도해석기 wrapper → TypedAction(+c, signals) 발행 노드."""

    def __init__(self) -> None:
        super().__init__('intent_llm_wrapper')

        self.declare_parameter('backbone', '')
        self.declare_parameter('scenario', '')
        self.declare_parameter('mode', 'direct')
        self.declare_parameter('utterance_topic', '/intent/user_prompt_raw')
        self.declare_parameter('output_topic', '/intent/llm_sigma_raw')
        self.declare_parameter('context_graph_topic', '/intent/context_graph')

        backbone = str(self.get_parameter('backbone').value)
        self._scenario = str(self.get_parameter('scenario').value)
        self._mode = str(self.get_parameter('mode').value)
        utterance_topic = str(self.get_parameter('utterance_topic').value)
        output_topic = str(self.get_parameter('output_topic').value)
        context_graph_topic = str(self.get_parameter('context_graph_topic').value)

        if not self._scenario.strip():
            raise ValueError('scenario 파라미터 필수 — 빈 문자열 불가')
        if self._mode not in _VALID_MODES:
            raise ValueError(
                f'mode={self._mode!r} 무효 — {_VALID_MODES} 중 하나'
            )
        if not backbone.strip():
            raise ValueError(
                f'backbone 파라미터 필수 — 등록: {list(list_registered())}'
            )
        # 미등록 backbone 측 KeyError (registry 측 등록 list 명시).
        self._wrapper = get_wrapper(backbone)

        self._context_graph = None
        self._pub = self.create_publisher(String, output_topic, 10)
        self.create_subscription(
            String, utterance_topic, self._on_utterance, 10,
        )
        if self._mode == 'fusion':
            self.create_subscription(
                String, context_graph_topic, self._on_context_graph, 10,
            )

        self.get_logger().info(
            f'wrapper_node ready — backbone={backbone} '
            f'({self._wrapper.category}) scenario={self._scenario} '
            f'mode={self._mode} in={utterance_topic} out={output_topic}'
        )

    def _on_context_graph(self, msg: String) -> None:
        try:
            self._context_graph = parse_context_graph(msg.data)
        except ValueError as exc:  # json.JSONDecodeError ⊂ ValueError
            self.get_logger().error(f'context_graph 파싱 실패 (무시): {exc}')

    def _on_utterance(self, msg: String) -> None:
        utterance = msg.data
        if not utterance or not utterance.strip():
            self.get_logger().warn('빈 utterance — drop')
            return
        context = self._context_graph if self._mode == 'fusion' else None
        intent_input = build_intent_input(utterance, self._scenario, context)
        # wrapper.process 측 *항상* 유효 IntentResult 산출 (interface contract,
        # 실패 측 ask_user + c_raw=0.0 fallback) — RQ1 정합.
        result = self._wrapper.process(intent_input)
        # σ.theta 에 target_class 주입 (ADR-0029 블로커 1) — estimator $s_1$ 클래스
        # 매칭용. fusion(context) 부재 시 추가 키 없음(serialize_result 와 동일).
        self._pub.publish(String(
            data=serialize_result_with_context(result, context),
        ))


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = WrapperNode()
    except Exception as exc:  # noqa: BLE001 — init 실패 측 명확 보고 후 재raise
        print(f'[wrapper_node] init 실패: {exc}', file=sys.stderr)
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
