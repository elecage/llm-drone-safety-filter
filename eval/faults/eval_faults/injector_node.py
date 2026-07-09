"""injector_node — paper §C 4 fault channel 통합 ROS 2 wrapper.

[ADR-0025 D5](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
12 PR 시안의 #5b 단계 — fault_scenario YAML 측 trial 정의 → channel 별 apply_*
dispatch → transformed σ/prompt/utterance/detection republish. host venv 측
순수 logic ([fault_scenario.py](fault_scenario.py) + [injector_helpers.py](injector_helpers.py))
+ ROS 2 wrapper (본 모듈) 분리 — A3-3 estimator_node 정합 패턴.

## launch 인자

  scenario_file (str, 필수): fault_scenario YAML 경로.
  seed (int, default -1): -1 측 scenario.seed 사용, 그 외 측 override.

## topic 구조 (4 channel)

| channel | input topic | output topic | msg type |
|---|---|---|---|
| hallucination | `/intent/llm_sigma_prefault` | `/intent/llm_sigma_raw` (인라인→actuation) | std_msgs/String (TypedAction JSON) |
| adversarial | `/intent/user_prompt_raw` | `/intent/user_prompt_faulted` | std_msgs/String (prompt) |
| cognitive_lapse | — (synthesis) | `/intent/lapse_event` | std_msgs/String (LapseEvent JSON) |
| attribute_mismatch | `/intent/ovd/detections` | `/intent/ovd/detections_faulted` | vision_msgs/Detection2DArray |
| none | — | — | (no-op — sub/pub 없음, alive only) |

attribute_mismatch 는 detector·estimator 와 동일한 `vision_msgs/Detection2DArray`
타입으로 실 OVD 파이프라인에 끼어든다 (ADR-0029 D-A5). 내부 `Detection` 리스트
위에서 도는 `apply_attribute_mismatch` 로직은 `detection_bridge` 로 양방향 변환.

cognitive_lapse 측 *synthesis* — subscribe 없이 노드 시작 후 *1-shot timer*
측 LapseEvent generate + publish.

## paper §C 본실험 측 흐름

```
$ ros2 launch eval_faults injector.launch.py scenario_file:=hallucination_target_swap_dangerous.yaml seed:=42
```

injector_node 시작 → scenario load → channel 별 sub/pub setup → trial 진행
(simulation 측 OVD/LLM/utterance source 노드 측 publish → injector 측 transform
→ downstream 측 subscribe). cognitive_lapse 측 1-shot LapseEvent publish.
"""

from __future__ import annotations

import dataclasses
import random
import sys

import rclpy
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

from std_msgs.msg import String

from eval_faults.adversarial import apply_adversarial
from eval_faults.attribute_mismatch import apply_attribute_mismatch
from eval_faults.cognitive_lapse import apply_cognitive_lapse
from eval_faults.detection_bridge import (
    detection2d_array_to_internal,
    internal_to_detection2d_array,
)
from eval_faults.fault_scenario import (
    FAULT_CHANNEL_FAULTED_TOPIC,
    HALLUCINATION_PREFAULT_TOPIC,
    FaultChannel,
    build_fault_context,
    load_fault_scenario,
)
from eval_faults.hallucination import apply_hallucination
from eval_faults.injector_helpers import (
    lapse_event_to_json,
    typed_action_from_json,
    typed_action_to_json,
)


class InjectorNode(Node):
    """4 fault channel 통합 wiring 노드 — fault_scenario YAML 측 channel 활성.

    한 InjectorNode 인스턴스 측 *한 channel 만* (한 trial = 한 fault_class).
    NONE channel 측 *no-op* — 노드 시작 후 sub/pub 등록 없이 spin (paper §C
    baseline trial 측 alive 보장).

    paper §C baseline trial 측 *downstream subscribe 정책*: NONE channel 측
    injector 측 sub/pub 없으므로, downstream 측 source 측 *원본 topic* (예:
    ``/intent/llm_sigma_raw``) 측 직접 subscribe 또는 launch 측 topic remap
    측 처리. fault channel 측 downstream 측 *_faulted topic* 측 subscribe.
    paper §C trial runner (B7 후속) 측 trial 별 launch 측 topic remap 측 처분
    (PR #106 review C-4 backlog).
    """

    def __init__(self) -> None:
        super().__init__('eval_faults_injector')

        self.declare_parameter('scenario_file', '')
        self.declare_parameter('seed', -1)
        # hallucination 인라인 체인 토픽 (세션 49): in = 상류 wrapper σ 출력,
        # out = 하류(다음 σ 스테이지 — tier2 활성 시 gate command, 아니면 actuation
        # /intent/llm_sigma_raw). 기본값은 단독/B0-B2 운용용(prefault→raw).
        self.declare_parameter('sigma_in_topic', HALLUCINATION_PREFAULT_TOPIC)
        self.declare_parameter(
            'sigma_out_topic', FAULT_CHANNEL_FAULTED_TOPIC[FaultChannel.HALLUCINATION],
        )
        # amendment 20 (Track B) — 사용자 *world* 위치 override (3-float). 설정 시
        # fault context 의 user_position 을 대체 — 사용자 지향 적대 변형이 시나리오별
        # 실제 사용자 위치(scenario_params 단일 출처)를 겨누게 한다. 미설정(넓은
        # 격자)이면 YAML 값 사용(하위 호환). 좌표는 launch_composition 이 주입(D-T3).
        # dynamic_typing — 빈 기본값 []('미설정')은 rclpy 가 BYTE_ARRAY 로 추론하나
        # launch override 는 DOUBLE_ARRAY([x,y,z]) → 고정 타입이면 충돌(실측 버그).
        # dynamic_typing=True 로 미설정(빈) ↔ 설정(3-float) 양쪽 허용.
        self.declare_parameter(
            'user_position_world', [],
            ParameterDescriptor(dynamic_typing=True),
        )

        scenario_path = str(self.get_parameter('scenario_file').value)
        if not scenario_path:
            raise RuntimeError(
                'scenario_file launch arg 필수 — fault_scenario YAML 경로'
            )

        self.scenario = load_fault_scenario(scenario_path)
        # NOTE: `self.context` 금지 — rclpy.node.Node.context 가 read-only property
        # 라 충돌(`can't set attribute`). fault context 는 `_fault_context` 로.
        self._fault_context, self.variant = build_fault_context(self.scenario)
        self._apply_user_position_override()
        seed_arg = int(self.get_parameter('seed').value)
        seed = self.scenario.seed if seed_arg == -1 else seed_arg
        self.rng = random.Random(seed)

        self.get_logger().info(
            f'InjectorNode 시작 — channel={self.scenario.channel.value}, '
            f'variant={self.scenario.variant}, seed={seed}, '
            f'scenario={self.scenario.name}'
        )

        self._setup_channel()

    # ------------------------------------------------------------ user pos override

    def _apply_user_position_override(self) -> None:
        """amendment 20 — `user_position_world` 설정 시 fault context override.

        Track B 사용자 지향 적대 변형(position_worst_user_direct)이 시나리오별 실제
        사용자 *world* 위치를 겨누게 한다. 좌표는 launch_composition 이 scenario_params
        (단일 출처)에서 도출해 주입. 미설정(빈 리스트)이면 no-op — YAML 값 유지.

        user_position 필드가 없는 context(cognitive_lapse)에는 적용 안 함(해당 채널은
        본 파라미터를 받지 않음 — 방어적 guard).
        """
        override = list(self.get_parameter('user_position_world').value)
        if not override:
            return
        if len(override) != 3:
            raise RuntimeError(
                f'user_position_world 는 3-float 여야 함 — got {override!r}'
            )
        if not dataclasses.is_dataclass(self._fault_context) or not hasattr(
            self._fault_context, 'user_position'
        ):
            self.get_logger().warn(
                'user_position_world 설정됐으나 fault context 에 user_position '
                f'없음 (channel={self.scenario.channel.value}) — override 생략.'
            )
            return
        self._fault_context = dataclasses.replace(
            self._fault_context, user_position=tuple(float(v) for v in override),
        )
        self.get_logger().info(
            f'user_position override → {tuple(self._fault_context.user_position)} '
            '(scenario_params world, amendment 20 Track B)'
        )

    # ------------------------------------------------------------ dispatch

    def _setup_channel(self) -> None:
        channel = self.scenario.channel
        if channel == FaultChannel.NONE:
            self.get_logger().info(
                'channel=none — no-op (sub/pub 등록 없음, alive only). '
                'downstream 측 raw topic 직접 subscribe 또는 launch remap 측 처분.'
            )
            return
        if channel == FaultChannel.HALLUCINATION:
            self._setup_hallucination()
        elif channel == FaultChannel.ADVERSARIAL:
            self._setup_adversarial()
        elif channel == FaultChannel.COGNITIVE_LAPSE:
            self._setup_cognitive_lapse()
        elif channel == FaultChannel.ATTRIBUTE_MISMATCH:
            self._setup_attribute_mismatch()
        else:
            raise RuntimeError(f'unknown FaultChannel: {channel!r}')

    # ------------------------------------------------------------ hallucination

    def _setup_hallucination(self) -> None:
        # 인라인(세션 49): wrapper 의 pre-injector 출력(HALLUCINATION_PREFAULT_TOPIC)
        # 을 받아 변형 후 actuation 토픽(/intent/llm_sigma_raw =
        # FAULT_CHANNEL_FAULTED_TOPIC[HALLUCINATION])으로 republish → sigma_bridge
        # (actuation) + estimator(c̃) 양쪽 도달. in≠out 이라 loop 없음.
        out_topic = str(self.get_parameter('sigma_out_topic').value)
        in_topic = str(self.get_parameter('sigma_in_topic').value)
        self._pub_sigma = self.create_publisher(String, out_topic, 10)
        self._sub_sigma = self.create_subscription(
            String, in_topic, self._on_sigma_raw, 10,
        )

    def _on_sigma_raw(self, msg: String) -> None:
        # PR #106 review C-3 — specific exception catch (broad Exception 측
        # unexpected silent drop 회피). msg 측 형식 오류 만 log+drop, 기타
        # unexpected exception 측 rclpy 측 propagate (노드 측 log + alive 유지).
        try:
            action = typed_action_from_json(msg.data)
            faulted = apply_hallucination(
                action, self.variant, self._fault_context, self.rng,
            )
            self._pub_sigma.publish(String(data=typed_action_to_json(faulted)))
        except (ValueError, KeyError, TypeError) as exc:
            self.get_logger().error(
                f'sigma fault inject 실패 (msg 측 형식 오류): {exc}'
            )

    # ------------------------------------------------------------ adversarial

    def _setup_adversarial(self) -> None:
        self._pub_prompt = self.create_publisher(
            String, FAULT_CHANNEL_FAULTED_TOPIC[FaultChannel.ADVERSARIAL], 10,
        )
        self._sub_prompt = self.create_subscription(
            String, '/intent/user_prompt_raw', self._on_prompt_raw, 10,
        )

    def _on_prompt_raw(self, msg: String) -> None:
        try:
            injected = apply_adversarial(
                msg.data, self.variant, self._fault_context, self.rng,
            )
            self._pub_prompt.publish(String(data=injected))
        except (ValueError, KeyError, TypeError) as exc:
            self.get_logger().error(
                f'prompt inject 실패 (msg 측 형식 오류): {exc}'
            )

    # ------------------------------------------------------------ cognitive_lapse

    def _setup_cognitive_lapse(self) -> None:
        """cognitive_lapse 는 *synthesis* — subscribe 없이 1-shot timer 측
        LapseEvent generate + publish.

        1초 delay 측 *subscriber 측 ready 보장* — colcon test 측 race condition
        회피. trial 측 단일 LapseEvent.

        **latched(transient_local) QoS** — 1-shot LapseEvent 가 rosbag2 recorder
        구독 *전* 발행되면 누락됨(세션 49 진단: 발행 771.648 vs rosbag 구독 771.892
        → /intent/lapse_event 0 sample → bag incomplete, cognitive_lapse 17 trial
        전량). transient_local 로 발행하면 늦게 합류한 구독자(recorder·tier2)가
        latch 된 마지막 이벤트를 수신 → 녹화·소비 보장. (구독형 채널은 지속 토픽이라
        무관, 1회성 cognitive_lapse 만 본 race 영향.)
        """
        latched_qos = QoSProfile(
            depth=1,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._pub_event = self.create_publisher(
            String,
            FAULT_CHANNEL_FAULTED_TOPIC[FaultChannel.COGNITIVE_LAPSE],
            latched_qos,
        )
        self._lapse_event = apply_cognitive_lapse(
            self.variant, self._fault_context, self.rng,
        )
        self._lapse_published = False
        self._publish_once_timer = self.create_timer(
            1.0, self._publish_lapse_event_once,
        )

    def _publish_lapse_event_once(self) -> None:
        if self._lapse_published:
            return
        msg = String(data=lapse_event_to_json(self._lapse_event))
        self._pub_event.publish(msg)
        self._lapse_published = True
        # PR #106 review C-2 — rclpy create_timer 측 반복 timer 라 첫 publish 후
        # 명시적 cancel 필수 (영구 1초 callback overhead 회피).
        self._publish_once_timer.cancel()
        self.get_logger().info(
            f'LapseEvent published — variant={self._lapse_event.variant.value}, '
            f'trigger_time_s={self._lapse_event.trigger_time_s:.2f}'
        )

    # ------------------------------------------------------------ attribute_mismatch

    def _setup_attribute_mismatch(self) -> None:
        # detector·estimator 와 동일한 Detection2DArray 타입 (ADR-0029 D-A5).
        # vision_msgs 는 ROS 2 환경 전용이라 채널 활성 시점에 import.
        from vision_msgs.msg import Detection2DArray
        self._pub_det = self.create_publisher(
            Detection2DArray,
            FAULT_CHANNEL_FAULTED_TOPIC[FaultChannel.ATTRIBUTE_MISMATCH], 10,
        )
        self._sub_det = self.create_subscription(
            Detection2DArray, '/intent/ovd/detections', self._on_detections_raw, 10,
        )

    def _on_detections_raw(self, msg) -> None:
        try:
            dets = detection2d_array_to_internal(msg)
            faulted = apply_attribute_mismatch(
                dets, self.variant, self._fault_context, self.rng,
            )
            self._pub_det.publish(
                internal_to_detection2d_array(faulted, msg.header)
            )
        except (ValueError, KeyError, TypeError) as exc:
            self.get_logger().error(
                f'detections fault inject 실패 (msg 측 형식 오류): {exc}'
            )


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = InjectorNode()
    except RuntimeError as exc:
        print(f'InjectorNode init 실패: {exc}', file=sys.stderr)
        rclpy.shutdown()
        sys.exit(1)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # trial teardown(LaunchService SIGINT/SIGTERM) 정상 종료 — Traceback 미출력
        # (격자 로그가 매 trial 무해한 ExternalShutdownException 으로 오염되어 실제
        # 에러를 가리던 것 정리).
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
