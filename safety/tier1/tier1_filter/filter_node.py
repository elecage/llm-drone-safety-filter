"""티어1 안전 필터 노드 — B0 (pass-through) / B1 (정적 CBF-QP) / B2 (신뢰도 변조 CBF-QP).

ADR-0011 D1 architecture:

    [nominal source: G2 player / *의도해석기* / fault-injection]
       → /cmd/trajectory_setpoint_nominal   (TwistStamped, ENU, local frame)
       → /cmd/pose_setpoint_nominal         (PoseStamped, ENU, local frame)
       → [본 노드 — B0/B1/B2 분기]
       → /cmd/trajectory_setpoint_safe      (TwistStamped, ENU, local frame)
       → /cmd/pose_setpoint_safe            (PoseStamped, ENU, local frame)
       → [G1: ENU→NED 변환 + PX4 packing]

모드 (launch param ``mode``):
  - ``b0``: pass-through (필터 없음, 안전 위반 baseline 입증용).
  - ``b1``: 정적 CBF-QP — $r = r_\\text{min}$ 고정 (cmsm-proof §5 명제 1). baseline B1a
            (정적 최소 마진 = 효율 baseline, ADR-0025 amendment 19).
  - ``b1_max``: 정적 CBF-QP — $r = r_\\text{max}$ 고정. baseline B1b (정적 최대 마진 =
            안전 baseline, ADR-0025 amendment 19). ``b1`` 과 동일 정적 CBF 로직이되
            반경만 $r_\\text{max}$ — SR·$\\bar r$ 를 *실 비행* 으로 측정하기 위해 메트릭
            상수가 아니라 실제 $r_\\text{max}$ 로 비행.
  - ``b2``: 신뢰도 변조 CBF-QP — $r(\\tilde c) = r_\\text{min} + (1-\\tilde c)(r_\\text{max}-r_\\text{min})$
            (cmsm-proof §6 정리 2). $\\tilde c$는 변화율 제한기를 거친 신뢰도.

CBF spec (cmsm-proof §7.1 P1-P5, 2026-05-25 잠금):
  - $r_\\text{min} = 0.9$ m (b_human 0.75m + drone radius 0.142m + brake 0.025m).
  - $r_\\text{max} = 1.5$ m (시안; 시나리오 task spec에서 조정).
  - $\\gamma = 4.0$ /s (PX4 closed-loop $1/\\tau_\\text{ctrl}$).
  - $u_\\text{max} = 0.5$ m/s (EASA C2 conservative scaling).
  - $\\dot{\\tilde c}_\\text{max} = u_\\text{max} / (r_\\text{max} - r_\\text{min}) = 0.833$ /s
    (§6 가용성 조건에서 자동 derive).

신뢰도 입력 (B2 전용):
  - 구독 토픽: ``/intent/grounding_confidence`` (``std_msgs/Float32``, $c \\in [0,1]$).
  - 변화율 제한기: 매 update마다 $\\dot c_\\text{raw} = (c_\\text{new} - \\tilde c_\\text{prev}) / \\Delta t$
    계산 후 $|\\dot c| \\le \\dot c_\\text{max}$로 clamp. $\\tilde c$, $\\dot{\\tilde c}$ 저장.
  - 미수신 default: $\\tilde c = 1.0$ ($r = r_\\text{min}$, B1과 동일 동작 — fail-active).
    * fail-safe로 $\\tilde c = 0.0$ ($r = r_\\text{max}$)을 쓰지 *않는* 이유: 노드 부팅 직후
      *의도해석기* mock이 아직 publish 안 한 일시적 상태와, *의도해석기*가 실제로 모호 명령에
      반응해 $c$를 낮춘 상태를 구분 못 함. 노드 부팅 직후엔 B1 동작이 안전 측면에서 충분
      (정형 보장 = $\\mathcal{C}_\\text{floor}$ 전방불변성).
  - 비유한값 (NaN/Inf) 수신: 0.0(최대 마진)으로 복구 — *부재*(위 fail-active)와 달리
    *비정상*은 상류 고장 신호이므로 보수 방향 (confidence_guard.sanitize_confidence).

drone 위치 (``vehicle_local_position``) 미수신 (B1·B2): CBF 평가 불가 → nominal
twist 를 **영속도(안전 정지비행)로 대체** 발행. pose 는 projection 이 drone 위치와
무관하므로 정상 처리. (설계 근거: RESEARCH_CONTEXT §B11-8 — 위치 추적 실패 시
즉시 안전 호버를 티어1에 내장. 종전 pass-through 는 필터 우회 fail-unsafe 경로였음,
2026-06-12 세션 34 정정.)

좌표계: 모든 토픽 ENU local frame (PX4 EKF origin 기준). `vehicle_local_position`
(NED)은 본 노드 내부에서 ENU로 변환 — $x_\\text{ENU} = y_\\text{NED}$,
$y_\\text{ENU} = x_\\text{NED}$, $z_\\text{ENU} = -z_\\text{NED}$.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy

from geometry_msgs.msg import PoseStamped, TwistStamped
from px4_msgs.msg import VehicleLocalPosition
from std_msgs.msg import Float32

from tier1_filter.cbf_qp import (
    cbf_qp_velocity_modulated,
    cbf_qp_velocity_static,
    project_pose_to_safe_modulated,
    project_pose_to_safe_static,
)
from tier1_filter.confidence_guard import sanitize_confidence
from tier1_filter.scenario_layout import (
    VALID_SCENARIO_IDS,
    cbf_availability_margin,
    is_cbf_available,
    tier1_cbf_params,
)


class FilterMode(Enum):
    B0 = 'b0'
    B1 = 'b1'
    B1_MAX = 'b1_max'
    B2 = 'b2'


def _px4_qos() -> QoSProfile:
    """PX4 uXRCE-DDS 호환 QoS — BEST_EFFORT + VOLATILE."""
    return QoSProfile(
        reliability=QoSReliabilityPolicy.BEST_EFFORT,
        durability=QoSDurabilityPolicy.VOLATILE,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=5,
    )


class Tier1FilterNode(Node):
    def __init__(self) -> None:
        super().__init__('tier1_filter')

        # --- 파라미터 ---
        self.declare_parameter('mode', 'b0')
        self.declare_parameter('input_twist_topic', '/cmd/trajectory_setpoint_nominal')
        self.declare_parameter('input_pose_topic', '/cmd/pose_setpoint_nominal')
        self.declare_parameter('output_twist_topic', '/cmd/trajectory_setpoint_safe')
        self.declare_parameter('output_pose_topic', '/cmd/pose_setpoint_safe')
        # scenario_id (S5–S8). 설정 시 CBF 파라미터(user_local·r_min·r_max·gamma·
        # u_max·dot_c_max)를 scenario_params 단일 소스(ADR-0023)에서 resolve해
        # 아래 explicit 기본값을 override. 빈 문자열·location('livingroom'/'yard')
        # 등 scenario_id 아닌 값이면 무시 → explicit 파라미터 사용(manual launch·
        # up.sh backward compat). runner(compose_trial_node_specs)는 scenario_id 전달.
        self.declare_parameter('scenario', '')
        # CBF spec (cmsm-proof §7.1).
        self.declare_parameter('r_min', 0.9)
        self.declare_parameter('r_max', 1.5)
        self.declare_parameter('gamma', 4.0)
        self.declare_parameter('u_max', 0.5)
        # $\\dot{\\tilde c}_\\text{max}$ = u_max / (r_max - r_min) (cmsm-proof §6 가용성).
        # ADR-0020 D9: 변화율 제한은 추정기 단일 → 티어 1은 이 값을 *가용성 검증*
        # (T2-4)에만 쓰고 신뢰도 재-clamp 에는 쓰지 않는다.
        self.declare_parameter('dot_c_max', 0.833)
        # ADR-0050 D2 실험 — 제동 버퍼 (기본 0.0 = off, 기존 거동 불변). CBF가 집행하는
        # 반경을 r + brake_buffer_m 로 키워, 단일적분기 CBF($\\dot p=u$, rel.deg 1)와 PX4
        # 속도추적 1차 지연($\\tau=1/\\gamma$) 사이 상대차수 간극이 만드는 경계 overshoot
        # ($\\approx v_\\text{approach}\\cdot\\tau$)를 흡수한다. 물리 하한 $r_\\text{min}$은
        # 불변 — *집행* 경계만 바깥으로 밀어 물리 하한 침범을 막는다(안전 강화, 공간 축소).
        # 솔버(cbf_qp)는 불변 — filter_node 가 r+buffer 를 넘길 뿐.
        self.declare_parameter('brake_buffer_m', 0.0)
        # user 위치 (local ENU). default = 거실 layout v3 user_marker world 좌표
        # (-2.6, 1.5, 1.1)을 EKF origin (spawn world 0.5, -0.5, 0.15) 기준 local로
        # 변환한 값 (-3.1, 2.0, 0.95). G2 c2 시나리오 USER_POS와 정합.
        self.declare_parameter('user_local_x', -3.1)
        self.declare_parameter('user_local_y', 2.0)
        self.declare_parameter('user_local_z', 0.95)
        # vehicle_local_position 토픽 (B1·B2용 — drone 현 위치).
        self.declare_parameter('vehicle_local_position_topic',
                               '/fmu/out/vehicle_local_position_v1')
        # 신뢰도 입력 토픽 (B2 전용).
        self.declare_parameter('grounding_confidence_topic',
                               '/intent/grounding_confidence')

        mode_str = str(self.get_parameter('mode').value).lower()
        try:
            self.mode = FilterMode(mode_str)
        except ValueError as exc:
            raise ValueError(
                f"mode 파라미터 값 '{mode_str}' 무효 — 'b0' | 'b1' | 'b1_max' | 'b2'"
            ) from exc

        self.input_twist_topic = str(self.get_parameter('input_twist_topic').value)
        self.input_pose_topic = str(self.get_parameter('input_pose_topic').value)
        self.output_twist_topic = str(self.get_parameter('output_twist_topic').value)
        self.output_pose_topic = str(self.get_parameter('output_pose_topic').value)
        self.r_min = float(self.get_parameter('r_min').value)
        self.r_max = float(self.get_parameter('r_max').value)
        self.gamma = float(self.get_parameter('gamma').value)
        self.u_max = float(self.get_parameter('u_max').value)
        self.dot_c_max = float(self.get_parameter('dot_c_max').value)
        self.brake_buffer = float(self.get_parameter('brake_buffer_m').value)
        self.user_pos = np.array([
            float(self.get_parameter('user_local_x').value),
            float(self.get_parameter('user_local_y').value),
            float(self.get_parameter('user_local_z').value),
        ])
        self.vlp_topic = str(self.get_parameter('vehicle_local_position_topic').value)
        self.gc_topic = str(self.get_parameter('grounding_confidence_topic').value)

        # scenario_id (S5–S8) 가 주어지면 CBF 파라미터를 단일 소스(ADR-0023)에서
        # resolve해 explicit 기본값을 override. runner 경로(compose_trial_node_specs
        # 가 {mode, scenario_id} 만 전달)를 correct-by-construction 으로 만든다.
        self._scenario = str(self.get_parameter('scenario').value).strip()
        if self._scenario in VALID_SCENARIO_IDS:
            cbf = tier1_cbf_params(self._scenario)
            self.r_min = cbf['r_min']
            self.r_max = cbf['r_max']
            self.gamma = cbf['gamma']
            self.u_max = cbf['u_max']
            self.dot_c_max = cbf['dot_c_max']
            self.user_pos = np.array([
                cbf['user_local_x'], cbf['user_local_y'], cbf['user_local_z'],
            ])
            self.get_logger().info(
                f"[scenario] {self._scenario} CBF 파라미터 resolve (ADR-0023): "
                f"r_min={self.r_min}, r_max={self.r_max}, "
                f"dot_c_max={self.dot_c_max:.4f}, user_local={self.user_pos.tolist()}"
            )

        # 정적 CBF 모드(B1·B1_MAX)의 고정 반경 — B1=r_min(효율), B1_MAX=r_max(안전).
        # scenario resolve 이후 계산(시나리오별 r_min/r_max override 반영).
        self._static_radius = (
            self.r_max if self.mode == FilterMode.B1_MAX else self.r_min
        )

        # B2 spec sanity: r_max >= r_min.
        if self.mode == FilterMode.B2 and self.r_max < self.r_min:
            raise ValueError(
                f"r_max={self.r_max} < r_min={self.r_min} — 단조 비증가 $r(c)$ 위반"
            )

        # T2-4: cmsm-proof §6 가용성 조건 (r_max − r_min)·dot_c_max ≤ u_max.
        # 변화율 제한된 신뢰도 거동이 입력 제약 안에서 실현 가능한지 — B2 전용.
        if self.mode == FilterMode.B2:
            margin = cbf_availability_margin(
                self.r_min, self.r_max, self.u_max, self.dot_c_max
            )
            if not is_cbf_available(
                self.r_min, self.r_max, self.u_max, self.dot_c_max
            ):
                raise ValueError(
                    f"가용성 위반 (cmsm-proof §6 / T2-4): "
                    f"(r_max−r_min)·dot_c_max = {(self.r_max - self.r_min) * self.dot_c_max:.4f} "
                    f"> u_max = {self.u_max} (margin={margin:.4f})"
                )
            self.get_logger().info(
                f"[availability] (r_max−r_min)·dot_c_max="
                f"{(self.r_max - self.r_min) * self.dot_c_max:.4f} ≤ u_max={self.u_max} "
                f"(margin={margin:.4f}) ✓"
            )

        # --- 상태 ---
        self._last_drone_pos_enu: Optional[np.ndarray] = None
        # 추정기가 변화율 제한한 $c(t)$ 의 최근 수신값 (ADR-0020 D9). 초기 1.0
        # (fail-active = B1 동작).
        self._c_tilde: float = 1.0
        self._dot_c_tilde: float = 0.0  # 마지막 측정 변화율 (clamp 없음, $\\dot r$ 항용).
        self._t_last_c_update: Optional[float] = None  # ROS time [s] (float).

        # --- Publishers (safe 토픽) ---
        self._pub_twist_safe = self.create_publisher(TwistStamped, self.output_twist_topic, 10)
        self._pub_pose_safe = self.create_publisher(PoseStamped, self.output_pose_topic, 10)

        # --- Subscribers (nominal 토픽) ---
        self._sub_twist_nominal = self.create_subscription(
            TwistStamped, self.input_twist_topic, self._on_twist_nominal, 10
        )
        self._sub_pose_nominal = self.create_subscription(
            PoseStamped, self.input_pose_topic, self._on_pose_nominal, 10
        )

        # B1·B1_MAX·B2는 drone 현 위치 필요.
        if self.mode in (FilterMode.B1, FilterMode.B1_MAX, FilterMode.B2):
            self._sub_vlp = self.create_subscription(
                VehicleLocalPosition, self.vlp_topic, self._on_vehicle_local_position,
                _px4_qos()
            )

        # B2는 신뢰도 입력 필요.
        if self.mode == FilterMode.B2:
            self._sub_gc = self.create_subscription(
                Float32, self.gc_topic, self._on_grounding_confidence, 10
            )

        # --- Startup log ---
        log_extra = ''
        if self.mode in (FilterMode.B1, FilterMode.B1_MAX):
            log_extra = f", static_radius={self._static_radius}"
        elif self.mode == FilterMode.B2:
            log_extra = (
                f", r_max={self.r_max}, dot_c_max={self.dot_c_max}, "
                f"gc_topic={self.gc_topic}, init c_tilde={self._c_tilde}"
            )
        self.get_logger().info(
            f"tier1_filter 시작 — mode={self.mode.value}, "
            f"r_min={self.r_min}, gamma={self.gamma}, u_max={self.u_max}, "
            f"user_pos={self.user_pos.tolist()}{log_extra}, "
            f"input twist={self.input_twist_topic}, pose={self.input_pose_topic}, "
            f"output twist={self.output_twist_topic}, pose={self.output_pose_topic}"
        )

    # ------------------------------------------------------------------
    # Vehicle local position callback (NED → local ENU 변환 + 캐싱)
    # ------------------------------------------------------------------
    def _on_vehicle_local_position(self, msg: VehicleLocalPosition) -> None:
        # NED → ENU: x_enu = y_ned, y_enu = x_ned, z_enu = -z_ned
        self._last_drone_pos_enu = np.array([msg.y, msg.x, -msg.z])

    # ------------------------------------------------------------------
    # Grounding confidence callback — 추정기 출력 $c(t)$ 수신 (ADR-0020 D9)
    # ------------------------------------------------------------------
    def _on_grounding_confidence(self, msg: Float32) -> None:
        # 수신값은 추정기가 이미 변화율 제한기를 통과시킨 $c(t)$ (ADR-0020 D9) —
        # 티어 1은 재차 변화율 제한하지 않고, 위생 처리(NaN/Inf → 0.0 최대 마진 +
        # $[0, 1]$ 보장)만 적용해 그대로 사용한다.
        c, finite = sanitize_confidence(float(msg.data))
        if not finite:
            self.get_logger().error(
                'grounding_confidence 비유한값 (NaN/Inf) 수신 — '
                'c=0.0 (최대 마진)으로 복구',
                throttle_duration_sec=2.0,
            )
        t_now = self.get_clock().now().nanoseconds * 1e-9

        if self._t_last_c_update is None:
            # 첫 수신 — 변화율 0, 값 그대로 채택.
            self._c_tilde = c
            self._dot_c_tilde = 0.0
            self._t_last_c_update = t_now
            return

        dt = t_now - self._t_last_c_update
        if dt <= 1e-6:
            return  # 시각 진행 없음, skip.

        # $\\dot{\\tilde c}$ 는 시변 CBF-QP 의 $\\dot r$ 항용으로 *측정* 만 한다
        # (clamp 없음). 추정기가 이미 $|\\dot c| \\le \\dot c_\\text{max}$ 를
        # 보장하므로(ADR-0020 D9) 티어 1의 추가 clamp 는 중복이다. 단 dt 를 수신
        # 처리 시각으로 잡으므로(Float32 에 timestamp 부재) 메시지 큐잉·지연 시
        # 측정 변화율이 상한을 넘을 수 있다 — ADR-0020 D9 "타이밍 한계", P5 실측 대상.
        self._dot_c_tilde = (c - self._c_tilde) / dt
        self._c_tilde = c
        self._t_last_c_update = t_now

    # ------------------------------------------------------------------
    # B2 helper — 현재 $\\tilde c$로부터 $r, \\dot r$ 계산
    # ------------------------------------------------------------------
    def _r_and_r_dot(self) -> tuple[float, float]:
        """현재 $\\tilde c$, $\\dot{\\tilde c}$에서 $r(\\tilde c)$, $\\dot r$ 계산.

        $r(c) = r_\\text{min} + (1-c)(r_\\text{max} - r_\\text{min})$
        $\\dot r = (dr/dc) \\dot c = -(r_\\text{max} - r_\\text{min}) \\dot{\\tilde c}$
        """
        span = self.r_max - self.r_min
        r = self.r_min + (1.0 - self._c_tilde) * span
        r_dot = -span * self._dot_c_tilde
        return r, r_dot

    # ------------------------------------------------------------------
    # Velocity nominal — B0 pass-through / B1 static CBF / B2 modulated CBF
    # ------------------------------------------------------------------
    def _on_twist_nominal(self, msg: TwistStamped) -> None:
        if self.mode == FilterMode.B0:
            self._pub_twist_safe.publish(msg)
            return

        if self._last_drone_pos_enu is None:
            # drone 위치 미수신 — CBF 평가 불가 → 영속도(안전 정지비행) 대체.
            # 종전 pass-through 는 필터 우회 fail-unsafe 경로 (§B11-8 설계와 괴리,
            # 2026-06-12 정정).
            self.get_logger().warn(
                f'{self.mode.value.upper()}: vehicle_local_position 미수신 — '
                f'nominal 차단, 영속도(안전 정지비행) 발행',
                throttle_duration_sec=2.0,
            )
            hover = TwistStamped()
            hover.header = msg.header
            self._pub_twist_safe.publish(hover)
            return

        u_nom = np.array([msg.twist.linear.x, msg.twist.linear.y, msg.twist.linear.z])

        if self.mode in (FilterMode.B1, FilterMode.B1_MAX):
            u_safe, info = cbf_qp_velocity_static(
                u_nom, self._last_drone_pos_enu, self.user_pos,
                self._static_radius + self.brake_buffer, self.gamma, self.u_max,
            )
        else:  # B2
            r, r_dot = self._r_and_r_dot()
            u_safe, info = cbf_qp_velocity_modulated(
                u_nom, self._last_drone_pos_enu, self.user_pos,
                r + self.brake_buffer, r_dot, self.gamma, self.u_max,
            )

        msg_safe = TwistStamped()
        msg_safe.header = msg.header
        msg_safe.twist.linear.x = float(u_safe[0])
        msg_safe.twist.linear.y = float(u_safe[1])
        msg_safe.twist.linear.z = float(u_safe[2])
        msg_safe.twist.angular = msg.twist.angular  # yawspeed pass-through (CBF는 yaw 무관).
        self._pub_twist_safe.publish(msg_safe)

        # CBF active 시점에만 로그 (정상 시 silence).
        if info.get('constraint_active'):
            extra = ''
            if self.mode == FilterMode.B2:
                extra = (f", c_tilde={self._c_tilde:.3f}, dot_c={self._dot_c_tilde:+.3f}, "
                         f"r={info['r']:.3f}, r_dot={info['r_dot']:+.3f}")
            self.get_logger().info(
                f"{self.mode.value.upper()} CBF active: h={info['h']:+.3f}m, "
                f"dist={info['dist']:.3f}m, lambda={info.get('lambda', 0):.3f}, "
                f"saturated={info['saturated']}{extra}",
                throttle_duration_sec=0.5,
            )

    # ------------------------------------------------------------------
    # Pose nominal — B0 pass-through / B1 static / B2 modulated boundary projection
    # ------------------------------------------------------------------
    def _on_pose_nominal(self, msg: PoseStamped) -> None:
        if self.mode == FilterMode.B0:
            self._pub_pose_safe.publish(msg)
            return

        p_target = np.array([
            msg.pose.position.x, msg.pose.position.y, msg.pose.position.z,
        ])

        if self.mode in (FilterMode.B1, FilterMode.B1_MAX):
            p_safe, info = project_pose_to_safe_static(
                p_target, self.user_pos, self._static_radius + self.brake_buffer
            )
        else:  # B2 — 현재 $r(\\tilde c)$로 projection.
            r, _ = self._r_and_r_dot()
            p_safe, info = project_pose_to_safe_modulated(
                p_target, self.user_pos, r + self.brake_buffer)

        msg_safe = PoseStamped()
        msg_safe.header = msg.header
        msg_safe.pose.position.x = float(p_safe[0])
        msg_safe.pose.position.y = float(p_safe[1])
        msg_safe.pose.position.z = float(p_safe[2])
        msg_safe.pose.orientation = msg.pose.orientation  # yaw pass-through
        self._pub_pose_safe.publish(msg_safe)

        if info.get('projected'):
            extra = ''
            if self.mode == FilterMode.B2:
                extra = f", c_tilde={self._c_tilde:.3f}"
            self.get_logger().info(
                f"{self.mode.value.upper()} pose projection: dist={info['dist']:.3f}m "
                f"→ r={info['r']:.3f}m boundary{extra}",
                throttle_duration_sec=0.5,
            )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = Tier1FilterNode()
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
