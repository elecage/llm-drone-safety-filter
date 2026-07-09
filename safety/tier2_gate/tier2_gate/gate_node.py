"""tier2_gate ROS 2 노드 — *의도해석기* 출력 → 게이트 결정 → dispatch.

cmsm-proof §9 / ADR-0013 / ADR-0019 / state.py M-contract 그대로.

토픽 인터페이스 (A4-2 = std_msgs 만, PX4/tier1 raw 토픽 adapter 는 A4-3):

입력:
- ``/intent/command`` ``std_msgs/String``  JSON ``{"sigma":..., "theta":..., "c":...}``
- ``/intent/user_response`` ``std_msgs/Bool``  ask_user 응답 (True=accept, False=decline)
- ``/intent/self_correction`` ``std_msgs/Empty``  자기수정 이벤트 (n_sc 증가)
- ``/tier2/sensor/drone_position_enu`` ``std_msgs/Float32MultiArray``  [x,y,z]
- ``/tier2/sensor/battery_pct`` ``std_msgs/Float32``
- ``/tier2/sensor/link_lost`` ``std_msgs/Bool``
- ``/tier1/state/active`` ``std_msgs/Bool``

출력:
- ``/tier2/gate/decision`` ``std_msgs/String``  JSON 결정 + reason + violations + echo
- ``/tier2/cmd/dispatch`` ``std_msgs/String``  JSON ``{"sigma":..., "theta":...}`` (ACCEPT 시)
"""

from __future__ import annotations

import json
from typing import Any

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, Empty, Float32, Float32MultiArray, String

from tier2_gate.catalog import Geofence
from tier2_gate.gate import Decision, gate, select_confidence
from tier2_gate.state import GateSession
from tier2_gate.thresholds import DEFAULT, Thresholds


class Tier2GateNode(Node):
    """tier2_gate runtime node — 결정 함수 G + 세션 상태 추적."""

    def __init__(self) -> None:
        super().__init__('tier2_gate_node')

        # ---- 파라미터 선언 (geofence + 카탈로그 컨텍스트 + 임계 override) ----
        # list-type 파라미터는 모두 stringified JSON 으로 통일 — launch DSL 의
        # LaunchConfiguration → 노드 substitution 경로가 string 만 안전히
        # 전달하기 때문 (M_launch fix).
        self.declare_parameter('geofence_xmin', -3.0)
        self.declare_parameter('geofence_xmax', 3.0)
        self.declare_parameter('geofence_ymin', -2.0)
        self.declare_parameter('geofence_ymax', 2.0)
        self.declare_parameter('geofence_zmin', 0.0)
        self.declare_parameter('geofence_zmax', 2.4)
        self.declare_parameter('known_objects_json', '["sofa", "mug", "tv"]')
        self.declare_parameter('target_poses_json', '{}')
        self.declare_parameter('dock_pos_json', '[0.0, 0.0, 0.0]')
        self.declare_parameter('progress_check_hz', 10.0)
        # 토픽 파라미터 (세션 49 — eval 격자 인라인 통합): 게이트를 σ 흐름에
        # 직렬화. command_topic = 상류(wrapper 또는 hallucination injector) σ 출력,
        # dispatch_topic = accept 시 하류(sigma_bridge actuation = /intent/llm_sigma_raw),
        # decision_topic = 결정 로그(eval /tier2/decision 정합). 기본값은 단독 운용용.
        self.declare_parameter('command_topic', '/intent/command')
        self.declare_parameter('dispatch_topic', '/tier2/cmd/dispatch')
        self.declare_parameter('decision_topic', '/tier2/gate/decision')
        # 신뢰도 c 는 estimator 가 별 토픽(/intent/grounding_confidence, Float32)으로
        # 발행한다 — σ payload(=`{sigma, theta}`)에는 없다. 게이트가 이 토픽을 구독해
        # 최신 c 를 Φ_4(c<c_lo→reject)·CONFIRM(c<c_hi) 판정에 쓴다. (세션 49 B4 인라인
        # 통합이 c 를 payload 에서 찾던 결함 정정 — 2026-06-22, ADR-0025 amendment.)
        self.declare_parameter('confidence_topic', '/intent/grounding_confidence')
        # c 미수신(첫 메시지 전 startup 창) fallback. estimator 가 10Hz 로 곧 발행하므로
        # 짧은 창에만 적용 — 시스템 fail-active 정책(신뢰도 부재=초기 1.0) 정합.
        self.declare_parameter('default_confidence', 1.0)

        self._geofence = Geofence(
            xmin=float(self.get_parameter('geofence_xmin').value),
            xmax=float(self.get_parameter('geofence_xmax').value),
            ymin=float(self.get_parameter('geofence_ymin').value),
            ymax=float(self.get_parameter('geofence_ymax').value),
            zmin=float(self.get_parameter('geofence_zmin').value),
            zmax=float(self.get_parameter('geofence_zmax').value),
        )
        known_raw = json.loads(self.get_parameter('known_objects_json').value)
        if not isinstance(known_raw, list) or any(not isinstance(k, str) for k in known_raw):
            raise ValueError(
                f'known_objects_json 은 list[str] JSON 이어야 함: {known_raw!r}'
            )
        self._known = frozenset(known_raw)
        self._thresholds: Thresholds = DEFAULT  # 임계 override 는 후속.

        target_poses_raw = json.loads(self.get_parameter('target_poses_json').value)
        if not isinstance(target_poses_raw, dict):
            raise ValueError(
                f'target_poses_json 은 dict[str, [x,y,z]] JSON 이어야 함: '
                f'{target_poses_raw!r}'
            )
        target_poses: dict[str, tuple[float, float, float]] = {
            k: (float(v[0]), float(v[1]), float(v[2]))
            for k, v in target_poses_raw.items()
        }
        dock_raw = json.loads(self.get_parameter('dock_pos_json').value)
        if not isinstance(dock_raw, list) or len(dock_raw) != 3:
            raise ValueError(
                f'dock_pos_json 은 [x, y, z] JSON 이어야 함: {dock_raw!r}'
            )
        dock = (float(dock_raw[0]), float(dock_raw[1]), float(dock_raw[2]))

        # ---- 세션 상태 ----
        self._session = GateSession(target_poses=target_poses, dock_pos_enu=dock)
        # estimator 가 발행하는 최신 신뢰도 (None = 아직 미수신 → default_confidence).
        self._latest_c: float | None = None
        self._default_c = float(self.get_parameter('default_confidence').value)

        # ---- QoS ----
        reliable = QoSProfile(reliability=ReliabilityPolicy.RELIABLE, depth=10)
        sensor_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=10)

        # ---- Publishers ----
        self._pub_decision = self.create_publisher(
            String, str(self.get_parameter('decision_topic').value), reliable
        )
        self._pub_dispatch = self.create_publisher(
            String, str(self.get_parameter('dispatch_topic').value), reliable
        )

        # ---- Subscribers ----
        self.create_subscription(
            String, str(self.get_parameter('command_topic').value),
            self._on_command, reliable,
        )
        self.create_subscription(
            Float32, str(self.get_parameter('confidence_topic').value),
            self._on_confidence, reliable,
        )
        self.create_subscription(
            Bool, '/intent/user_response', self._on_user_response, reliable
        )
        self.create_subscription(
            Empty, '/intent/self_correction', self._on_self_correction, reliable
        )
        self.create_subscription(
            Float32MultiArray, '/tier2/sensor/drone_position_enu',
            self._on_drone_pos, sensor_qos,
        )
        self.create_subscription(
            Float32, '/tier2/sensor/battery_pct', self._on_battery, sensor_qos,
        )
        self.create_subscription(
            Bool, '/tier2/sensor/link_lost', self._on_link_lost, sensor_qos,
        )
        self.create_subscription(
            Bool, '/tier1/state/active', self._on_tier1, reliable,
        )

        # ---- Progress timer ----
        hz = float(self.get_parameter('progress_check_hz').value)
        self.create_timer(1.0 / max(hz, 0.1), self._on_progress_tick)

        self.get_logger().info(
            f'tier2_gate_node ready — geofence '
            f'x[{self._geofence.xmin},{self._geofence.xmax}] '
            f'y[{self._geofence.ymin},{self._geofence.ymax}] '
            f'z[{self._geofence.zmin},{self._geofence.zmax}] '
            f'known={sorted(self._known)} '
            f'c_lo={self._thresholds.c_lo} c_hi={self._thresholds.c_hi}'
        )

    # ------------------------------------------------------------------
    # 메인 결정 흐름
    # ------------------------------------------------------------------

    def _on_command(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
            sigma = str(payload['sigma'])
            theta = payload.get('theta', {})
            if not isinstance(theta, dict):
                raise ValueError('theta must be object')
            theta = _normalize_theta(theta)
            # c 출처 우선순위 = estimator(_latest_c) > payload 'c' > default
            # (select_confidence 순수 함수 — 우선순위 근거·세션 52 결함 정정은 거기
            # docstring 참조). float(payload['c']) 예외는 아래 except 가 reject 처리.
            c = select_confidence(self._latest_c, payload, self._default_c)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            self.get_logger().warn(
                f'intent payload 무효: {e}; data={msg.data[:100]}'
            )
            self._publish_decision('reject', f'invalid payload: {e}', [],
                                   None, None, None)
            return

        now = self._now()
        gs = self._session.to_gate_state(now=now)
        result = gate(
            sigma, theta, c,
            sigma_prev=self._session.sigma_prev,
            theta_prev=self._session.theta_prev,
            activity=self._session.activity,
            geofence=self._geofence,
            known_objects=self._known,
            state=gs,
            thresholds=self._thresholds,
        )

        # state transition
        if result.decision == Decision.ACCEPT:
            self._session.on_accept(sigma, theta)
            dispatch_payload = json.dumps({'sigma': sigma, 'theta': theta})
            self._pub_dispatch.publish(String(data=dispatch_payload))
        elif result.decision == Decision.CONFIRM:
            self._session.on_confirm(now=now)
        # REJECT — 상태 변화 없음 (n_sc 누적은 /intent/self_correction 토픽으로 별도)

        self._publish_decision(
            result.decision.value, result.reason, list(result.violations),
            sigma, theta, c,
        )

    def _publish_decision(
        self, decision: str, reason: str, violations: list,
        sigma: Any, theta: Any, c: Any,
    ) -> None:
        payload = json.dumps({
            'decision': decision,
            'reason': reason,
            'violations': violations,
            'sigma': sigma,
            'theta': theta,
            'c': c,
        })
        self._pub_decision.publish(String(data=payload))

    # ------------------------------------------------------------------
    # 센서·이벤트 callbacks
    # ------------------------------------------------------------------

    def _on_confidence(self, msg: Float32) -> None:
        # estimator 가 발행한 최신 의도 해석 신뢰도 c. _on_command 가 Φ_4·CONFIRM
        # 판정에 사용(σ payload 의 c 부재 정정 — 2026-06-22).
        self._latest_c = float(msg.data)

    def _on_user_response(self, msg: Bool) -> None:
        self._session.on_user_response(bool(msg.data))

    def _on_self_correction(self, _msg: Empty) -> None:
        self._session.on_self_correction()

    def _on_drone_pos(self, msg: Float32MultiArray) -> None:
        if len(msg.data) >= 3:
            self._session.drone_pos_enu = (
                float(msg.data[0]), float(msg.data[1]), float(msg.data[2])
            )

    def _on_battery(self, msg: Float32) -> None:
        self._session.battery_pct = float(msg.data)

    def _on_link_lost(self, msg: Bool) -> None:
        self._session.link_lost = bool(msg.data)

    def _on_tier1(self, msg: Bool) -> None:
        self._session.tier1_active = bool(msg.data)

    # ------------------------------------------------------------------
    # Progress timer
    # ------------------------------------------------------------------

    def _on_progress_tick(self) -> None:
        self._session.update_activity_progress(
            thresholds=self._thresholds, now=self._now()
        )

    # ------------------------------------------------------------------
    # Clock — ROS clock (use_sim_time 정합). M_clock fix — GateSession 내부
    # 의 time.monotonic() fallback 을 대체해 sim time 가속·점프에 강건.
    # ------------------------------------------------------------------

    def _now(self) -> float:
        """ROS clock 의 현 시각 [s] — `time.monotonic()` 미사용."""
        return self.get_clock().now().nanoseconds * 1e-9


def _normalize_theta(theta: dict) -> dict:
    """move_to.position 같은 list → tuple 정규화 (JSON 라운드트립 후 type 일치)."""
    out = dict(theta)
    if 'position' in out and isinstance(out['position'], list):
        out['position'] = tuple(float(v) for v in out['position'])
    return out


def main() -> None:
    rclpy.init()
    node = Tier2GateNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        # launch SIGINT 시 rclpy 신호 핸들러가 이미 context 를 shutdown 했을 수
        # 있다 → 중복 호출은 RCLError(rcl_shutdown already called)로 죽어 trial
        # 마다 gate_node exit 1 노이즈. rclpy.ok() 로 가드 (B4 teardown 정리).
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
