"""G1 offboard control node — PX4 arm/OFFBOARD 진입 + ENU→NED velocity forwarder.

본 노드의 책임 (ADR-0011 D1·D2·D3·D4):
  1. ``/fmu/in/offboard_control_mode`` 를 ≥2 Hz로 publish.
  2. ``/cmd/trajectory_setpoint_safe`` (TwistStamped, ENU) 구독 → 변환 →
     ``/fmu/in/trajectory_setpoint`` (NED) publish.
  3. Arm + OFFBOARD 모드 진입 sequence — ``/fmu/in/vehicle_command``.
  4. Nominal 토픽 silence 시 position hold (takeoff_altitude) 자동 default.

State machine
-------------
  INIT       → offboard mode stream 시작, hover setpoint publish (PX4가 N개 setpoint 받기 전엔 OFFBOARD 진입 거절).
  ARMING     → OFFBOARD 모드 + ARM 명령 전송.
  CLIMB      → 목표 고도(``takeoff_altitude``)로 *position* 모드 명령 — PX4
               position controller가 smooth 감속 + 정확 도달. (이전 velocity
               climb은 handoff 시점 EKF 노이즈로 50 cm undershoot 발생.)
  ACTIVE     → nominal 토픽 활성 시 velocity 모드로 forward (ADR-0011 D2).
               nominal 부재 / stale 시 position hold (hold_xy, takeoff_alt).

전제: PX4 SITL이 이미 가동 중이고 uXRCE-DDS 세션이 established 상태
(``/fmu/out/vehicle_status_v1`` 구독으로 확인).

ADR-0011 §D2 (nominal forwarding only): nominal 활성 시 position·acceleration·
yaw NaN, velocity[3] + yawspeed만 권위. nominal 부재 hold는 position 모드로
명시 — CBF-QP 안전 필터는 nominal 활성 구간에서만 의미.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

from geometry_msgs.msg import PoseStamped, TwistStamped
from px4_msgs.msg import (
    OffboardControlMode,
    TrajectorySetpoint,
    VehicleCommand,
    VehicleLocalPosition,
    VehicleStatus,
)

from g1_offboard.frame_conversions import (
    enu_position_to_ned,
    enu_velocity_to_ned,
    enu_yawrate_to_ned,
)


def _px4_qos() -> QoSProfile:
    """PX4 uXRCE-DDS 호환 QoS — best_effort + volatile + keep_last(5).
    Streaming 입출력(offboard_control_mode, trajectory_setpoint, vehicle_status,
    vehicle_local_position 등)용."""
    return QoSProfile(
        reliability=QoSReliabilityPolicy.BEST_EFFORT,
        durability=QoSDurabilityPolicy.VOLATILE,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=5,
    )


def _px4_qos_reliable() -> QoSProfile:
    """vehicle_command 전용 — PX4 측 subscriber가 RELIABLE이므로 publisher도
    RELIABLE 필수 (2026-05-24 실측: BEST_EFFORT publisher 시 명령 0건 도달).
    명령이 lossy하면 안 되므로 PX4 main이 이 토픽만 RELIABLE로 정의함."""
    return QoSProfile(
        reliability=QoSReliabilityPolicy.RELIABLE,
        durability=QoSDurabilityPolicy.VOLATILE,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=5,
    )


class FlightState(Enum):
    INIT = 'init'
    ARMING = 'arming'
    CLIMB = 'climb'
    ACTIVE = 'active'


class OffboardControlNode(Node):
    def __init__(self) -> None:
        super().__init__('g1_offboard_control')

        # --- 파라미터 (ADR-0011 §D2·§D4) ---
        self.declare_parameter('input_topic', '/cmd/trajectory_setpoint_safe')
        self.declare_parameter('pose_input_topic', '/cmd/pose_setpoint_safe')
        self.declare_parameter('publish_rate_hz', 20.0)
        self.declare_parameter('takeoff_altitude_m', 1.5)
        self.declare_parameter('climb_velocity_mps', 1.0)
        self.declare_parameter('altitude_tolerance_m', 0.2)
        self.declare_parameter('arming_warmup_s', 1.0)
        self.declare_parameter('nominal_timeout_s', 0.5)
        # ARMING state에서 vehicle_command 재전송 주기. PX4 preflight 워밍업
        # (EKF·GPS) 진행 중엔 ARM denied 후 노드가 다시 시도해야 함.
        self.declare_parameter('arm_retry_period_s', 1.0)

        self.input_topic = self.get_parameter('input_topic').value
        self.pose_input_topic = self.get_parameter('pose_input_topic').value
        self.publish_period = 1.0 / float(self.get_parameter('publish_rate_hz').value)
        self.takeoff_altitude = float(self.get_parameter('takeoff_altitude_m').value)
        self.climb_velocity = float(self.get_parameter('climb_velocity_mps').value)
        self.altitude_tolerance = float(self.get_parameter('altitude_tolerance_m').value)
        self.arming_warmup = float(self.get_parameter('arming_warmup_s').value)
        self.nominal_timeout = float(self.get_parameter('nominal_timeout_s').value)
        self.arm_retry_period = float(self.get_parameter('arm_retry_period_s').value)

        px4_qos = _px4_qos()

        # --- Publishers (NED, PX4 input) ---
        self._pub_offboard_mode = self.create_publisher(
            OffboardControlMode, '/fmu/in/offboard_control_mode', px4_qos
        )
        self._pub_setpoint = self.create_publisher(
            TrajectorySetpoint, '/fmu/in/trajectory_setpoint', px4_qos
        )
        self._pub_vehicle_command = self.create_publisher(
            VehicleCommand, '/fmu/in/vehicle_command', _px4_qos_reliable()
        )

        # --- Subscribers ---
        # vehicle_status는 PX4 main에서 v4로 versioning 이전됨 (2026-05-24 실측 —
        # v1은 advertise만, publish 0; v4는 5 Hz 활성). 메시지 타입은 동일
        # (px4_msgs/msg/VehicleStatus). 다른 토픽(vehicle_local_position·attitude
        # 등)은 v1 유지.
        self._sub_status = self.create_subscription(
            VehicleStatus, '/fmu/out/vehicle_status_v4', self._on_status, px4_qos
        )
        self._sub_local_pos = self.create_subscription(
            VehicleLocalPosition, '/fmu/out/vehicle_local_position_v1', self._on_local_pos, px4_qos
        )
        self._sub_nominal = self.create_subscription(
            TwistStamped, self.input_topic, self._on_nominal, 10
        )
        # G2 position step용 — 누적 drift를 끊는 corner anchor.
        # velocity nominal과 동시 활성 시 더 최근(timestamp 큰 것) 우선.
        self._sub_pose_nominal = self.create_subscription(
            PoseStamped, self.pose_input_topic, self._on_pose_nominal, 10
        )

        # --- 상태 ---
        self._state = FlightState.INIT
        self._tick_count = 0
        self._last_status: Optional[VehicleStatus] = None
        self._last_local_pos: Optional[VehicleLocalPosition] = None
        self._last_nominal: Optional[TwistStamped] = None
        self._last_nominal_stamp_ns: int = 0
        self._last_pose_nominal: Optional[PoseStamped] = None
        self._last_pose_nominal_stamp_ns: int = 0
        self._start_ns: int = self.get_clock().now().nanoseconds
        self._last_arm_attempt_ns: int = 0
        # CLIMB / ACTIVE idle hover에서 hold할 (x, y, z) NED 좌표 — ARMING→CLIMB
        # 전이 시 현재 위치로 캡처. 부재 시 (0, 0) 폴백 (사실상 spawn 근처).
        # z는 기본 -takeoff_altitude (NED 음수=위로). pose nominal 도착 시 갱신.
        self._hold_x_ned: float = 0.0
        self._hold_y_ned: float = 0.0
        self._hold_z_ned: float = -self.takeoff_altitude
        # ACTIVE velocity→position 전이 추적 — v≈0 전환 시 현 위치를 hold 기준으로 갱신.
        self._active_in_velocity: bool = False

        # --- Timer ---
        self._timer = self.create_timer(self.publish_period, self._on_timer)

        self.get_logger().info(
            f'G1 offboard control 시작 — input_topic={self.input_topic}, '
            f'rate={1.0/self.publish_period:.1f}Hz, takeoff_altitude={self.takeoff_altitude}m'
        )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _on_status(self, msg: VehicleStatus) -> None:
        self._last_status = msg

    def _on_local_pos(self, msg: VehicleLocalPosition) -> None:
        self._last_local_pos = msg

    def _on_nominal(self, msg: TwistStamped) -> None:
        self._last_nominal = msg
        self._last_nominal_stamp_ns = self.get_clock().now().nanoseconds

    def _on_pose_nominal(self, msg: PoseStamped) -> None:
        self._last_pose_nominal = msg
        self._last_pose_nominal_stamp_ns = self.get_clock().now().nanoseconds

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _on_timer(self) -> None:
        self._tick_count += 1

        # sim 리셋(PX4 SITL 재시작) 복구 — 비행 중(CLIMB/ACTIVE) PX4 가 disarm 되면
        # (영속 g1 은 살아 있으나 PX4 가 재시작·지상·disarm 상태) ARMING 으로 재진입해
        # 재-arm·재climb. 본 노드가 sim 리셋에 견고해야 본실험 격자에서 trial 간 SITL
        # 재시작 후에도 드론이 자동 복귀한다 (ADR-0030 F6, 세션 46 실 sim 발견).
        # 신선한 status 가 DISARMED 일 때만 — 재연결 직후 stale/None 에는 반응 안 함.
        if self._state in (FlightState.CLIMB, FlightState.ACTIVE) \
                and self._last_status is not None \
                and self._last_status.arming_state != VehicleStatus.ARMING_STATE_ARMED:
            self.get_logger().warn(
                '[G1] disarm 감지 (PX4 재시작 추정) — ARMING 재진입·재climb',
                throttle_duration_sec=2.0,
            )
            self._enter_offboard_and_arm()
            self._last_arm_attempt_ns = self.get_clock().now().nanoseconds
            self._active_in_velocity = False
            self._state = FlightState.ARMING
            return

        # 상태 전이.
        if self._state == FlightState.INIT:
            self._publish_offboard_mode(velocity=True)
            self._publish_hover_setpoint()
            elapsed_s = (self.get_clock().now().nanoseconds - self._start_ns) * 1e-9
            # PX4는 OFFBOARD 진입 전 setpoint stream이 흐르고 있어야 함 (>1초).
            if elapsed_s >= self.arming_warmup:
                self._enter_offboard_and_arm()
                self._last_arm_attempt_ns = self.get_clock().now().nanoseconds
                self._state = FlightState.ARMING
                self.get_logger().info('[G1] ARMING — OFFBOARD + ARM 명령 송신')

        elif self._state == FlightState.ARMING:
            self._publish_offboard_mode(velocity=True)
            self._publish_hover_setpoint()
            # CLIMB 전이 조건: ARMED + OFFBOARD + _last_local_pos 가용. 세 번째
            # 조건은 ROS 2 측에서 vehicle_local_position 첫 메시지가 deserialize
            # 된 후에야 충족 — vehicle_status가 먼저 도착해 ARMED로 보이는 동안
            # _last_local_pos가 None이면 hold_xy 기본값 (0,0) 캡처 → CLIMB가
            # spawn 위가 아니라 world (0, 0) 위로 hover하는 bug 발생 (2026-05-24
            # c2 진단). ARMED + local_pos None은 재시도 의미 없으므로 다음 tick 대기.
            if self._is_armed_and_offboard() and self._last_local_pos is not None:
                self._hold_x_ned = self._last_local_pos.x
                self._hold_y_ned = self._last_local_pos.y
                self._state = FlightState.CLIMB
                self.get_logger().info(
                    f'[G1] CLIMB — 목표 고도 {self.takeoff_altitude}m, '
                    f'hold_ned=({self._hold_x_ned:+.2f}, {self._hold_y_ned:+.2f})'
                )
            elif not self._is_armed_and_offboard():
                # PX4 preflight (EKF·GPS 워밍업)가 진행 중일 수 있음 — 주기적으로
                # OFFBOARD + ARM 명령 재전송. health 통과되면 다음 시도가 ACCEPTED.
                age_s = (self.get_clock().now().nanoseconds - self._last_arm_attempt_ns) * 1e-9
                if age_s >= self.arm_retry_period:
                    self._enter_offboard_and_arm()
                    self._last_arm_attempt_ns = self.get_clock().now().nanoseconds
                    self.get_logger().info(
                        '[G1] ARMING — 재시도 (preflight 통과 대기 중)',
                        throttle_duration_sec=5.0,
                    )

        elif self._state == FlightState.CLIMB:
            # CLIMB은 position 모드로 직접 takeoff_altitude 지정 — PX4 position
            # controller가 부드럽게 감속해 정확히 도달. (이전 velocity-only CLIMB은
            # handoff 시점 EKF 노이즈로 50 cm undershoot 발생, 2026-05-24 amendment.)
            # _hold_z_ned는 __init__에서 -takeoff_altitude로 초기화.
            self._publish_offboard_mode(position=True)
            self._publish_position_setpoint_ned(
                self._hold_x_ned, self._hold_y_ned, self._hold_z_ned
            )
            if self._has_reached_altitude():
                self._state = FlightState.ACTIVE
                self.get_logger().info('[G1] ACTIVE — nominal 토픽 forwarding')

        elif self._state == FlightState.ACTIVE:
            self._publish_active_setpoint()

    # ------------------------------------------------------------------
    # Setpoint publishers (ADR-0011 §D2: velocity-only)
    # ------------------------------------------------------------------
    def _publish_offboard_mode(self, position: bool = False, velocity: bool = False) -> None:
        msg = OffboardControlMode()
        msg.timestamp = self._px4_timestamp_us()
        msg.position = bool(position)
        msg.velocity = bool(velocity)
        msg.acceleration = False
        msg.attitude = False
        msg.body_rate = False
        msg.thrust_and_torque = False
        msg.direct_actuator = False
        self._pub_offboard_mode.publish(msg)

    def _publish_hover_setpoint(self) -> None:
        self._publish_velocity_setpoint_ned(0.0, 0.0, 0.0, 0.0)

    # nominal velocity가 이 값 이하면 "정지 명령"으로 판단 → position hold.
    # velocity 모드로 v=0을 보내면 PX4 속도 제어기가 위치 보정 없이 진동함.
    _VEL_ZERO_THRESH: float = 0.05  # m/s

    def _publish_active_setpoint(self) -> None:
        # 두 nominal 토픽 (velocity / position) 중 더 최근에 도착한 것 우선.
        # G2가 position step일 땐 pose만, velocity step일 땐 twist만 publish하므로
        # 우선순위는 사실상 "최근 publisher" 선택. 동일 시점에 둘 다 신선하면 pose
        # 우선(corner anchor 의도가 명확).
        now_ns = self.get_clock().now().nanoseconds
        twist_age = (now_ns - self._last_nominal_stamp_ns) * 1e-9 if self._last_nominal else float('inf')
        pose_age = (now_ns - self._last_pose_nominal_stamp_ns) * 1e-9 if self._last_pose_nominal else float('inf')
        twist_alive = twist_age <= self.nominal_timeout
        pose_alive = pose_age <= self.nominal_timeout

        if pose_alive and (not twist_alive or pose_age <= twist_age):
            # Position nominal forwarding — corner anchor용 절대 위치.
            p = self._last_pose_nominal.pose.position
            x_ned, y_ned, z_ned = enu_position_to_ned(p.x, p.y, p.z)
            # Hold anchor 갱신 — 이후 stale 시에도 이 위치 유지.
            self._hold_x_ned = x_ned
            self._hold_y_ned = y_ned
            self._hold_z_ned = z_ned
            self._active_in_velocity = False
            self._publish_offboard_mode(position=True)
            # yaw: quaternion이 all-zero면 G2가 NaN 의도 신호 → yaw NaN(현 yaw 유지).
            q = self._last_pose_nominal.pose.orientation
            if q.x == 0.0 and q.y == 0.0 and q.z == 0.0 and q.w == 0.0:
                yaw_ned = float('nan')
            else:
                # ENU yaw → NED yaw: ENU는 East 기준 CCW, NED는 North 기준 CW.
                # yaw_ned = π/2 - yaw_enu (mod 2π). 본 시나리오들은 모두 NaN 사용.
                yaw_enu = 2.0 * math.atan2(q.z, q.w)
                yaw_ned = (math.pi / 2.0) - yaw_enu
            self._publish_position_setpoint_ned(x_ned, y_ned, z_ned, yaw_ned)
            return

        if twist_alive:
            v_enu = self._last_nominal.twist.linear
            omega_z_enu = self._last_nominal.twist.angular.z
            vx_ned, vy_ned, vz_ned = enu_velocity_to_ned(v_enu.x, v_enu.y, v_enu.z)
            yawspeed_ned = enu_yawrate_to_ned(omega_z_enu)
            v_mag = math.sqrt(vx_ned ** 2 + vy_ned ** 2 + vz_ned ** 2)

            if v_mag >= self._VEL_ZERO_THRESH:
                # 이동 명령 — velocity 모드 forwarding.
                self._active_in_velocity = True
                self._publish_offboard_mode(velocity=True)
                self._publish_velocity_setpoint_ned(vx_ned, vy_ned, vz_ned, yawspeed_ned)
            else:
                # v≈0 명령 — position hold로 전환. velocity=0 OFFBOARD는 위치
                # 보정 없어 진동 유발. velocity→position 전이 첫 tick에 현 위치를
                # hold 기준으로 캡처 (drift된 *현재 위치* 그대로 — corner anchor
                # 의도라면 G2가 position step을 보내야 함).
                if self._active_in_velocity and self._last_local_pos is not None:
                    self._hold_x_ned = self._last_local_pos.x
                    self._hold_y_ned = self._last_local_pos.y
                self._active_in_velocity = False
                self._publish_offboard_mode(position=True)
                self._publish_position_setpoint_ned(
                    self._hold_x_ned, self._hold_y_ned, self._hold_z_ned
                )
            return

        # 둘 다 stale — position hold (마지막 anchor 유지).
        # nominal 을 한 번이라도 받은 뒤의 stale 은 상류(필터·player) 지연·중단
        # 신호 — 무로깅이면 "응답 없음" 체감의 원인 진단 불가 (세션 34 리뷰 후속).
        if self._last_nominal is not None or self._last_pose_nominal is not None:
            self.get_logger().warn(
                f'nominal stale (twist age={twist_age:.2f}s, pose age={pose_age:.2f}s '
                f'> timeout={self.nominal_timeout}s) → position hold',
                throttle_duration_sec=2.0,
            )
        self._active_in_velocity = False
        self._publish_offboard_mode(position=True)
        self._publish_position_setpoint_ned(
            self._hold_x_ned, self._hold_y_ned, self._hold_z_ned
        )

    def _publish_position_setpoint_ned(
        self, x: float, y: float, z: float, yaw: float = float('nan')
    ) -> None:
        """Position hold용 — CLIMB / ACTIVE idle hover."""
        nan = float('nan')
        msg = TrajectorySetpoint()
        msg.timestamp = self._px4_timestamp_us()
        msg.position = [float(x), float(y), float(z)]
        msg.velocity = [nan, nan, nan]
        msg.acceleration = [nan, nan, nan]
        msg.jerk = [nan, nan, nan]
        msg.yaw = float(yaw)
        msg.yawspeed = nan
        self._pub_setpoint.publish(msg)

    def _publish_velocity_setpoint_ned(
        self, vx: float, vy: float, vz: float, yawspeed: float
    ) -> None:
        """권위 있는 필드는 velocity + yawspeed만. 나머지는 NaN (ADR-0011 §D2)."""
        nan = float('nan')
        msg = TrajectorySetpoint()
        msg.timestamp = self._px4_timestamp_us()
        msg.position = [nan, nan, nan]
        msg.velocity = [float(vx), float(vy), float(vz)]
        msg.acceleration = [nan, nan, nan]
        msg.jerk = [nan, nan, nan]
        msg.yaw = nan
        msg.yawspeed = float(yawspeed)
        self._pub_setpoint.publish(msg)

    # ------------------------------------------------------------------
    # Arm + OFFBOARD entry
    # ------------------------------------------------------------------
    def _enter_offboard_and_arm(self) -> None:
        # PX4 custom_mode = 6.0 (OFFBOARD), base_mode = 1.0 (custom mode enabled).
        self._send_vehicle_command(
            VehicleCommand.VEHICLE_CMD_DO_SET_MODE, param1=1.0, param2=6.0
        )
        self._send_vehicle_command(
            VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, param1=1.0
        )

    def _send_vehicle_command(
        self, command: int, param1: float = 0.0, param2: float = 0.0
    ) -> None:
        msg = VehicleCommand()
        msg.timestamp = self._px4_timestamp_us()
        msg.command = command
        msg.param1 = float(param1)
        msg.param2 = float(param2)
        msg.target_system = 1
        msg.target_component = 1
        # source_system은 PX4의 MAV_SYS_ID(=1)와 *달라야* 함 — 같으면 PX4가
        # self-loop 방지로 명령 무시 (2026-05-24 실측: 마지막 vehicle_command가
        # 96초 전 = G1 명령 도달 안 함). MAVLink GCS 표준 = 255.
        msg.source_system = 255
        msg.source_component = 1
        msg.from_external = True
        self._pub_vehicle_command.publish(msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _px4_timestamp_us(self) -> int:
        """PX4 메시지의 timestamp 필드 — 시스템 시작 후 마이크로초.
        ROS 2 시간을 그대로 us로 변환(절대 epoch). PX4 client는 자체 동기화.
        """
        return self.get_clock().now().nanoseconds // 1000

    def _is_armed_and_offboard(self) -> bool:
        if self._last_status is None:
            return False
        armed = self._last_status.arming_state == VehicleStatus.ARMING_STATE_ARMED
        offboard = self._last_status.nav_state == VehicleStatus.NAVIGATION_STATE_OFFBOARD
        return armed and offboard

    def _has_reached_altitude(self) -> bool:
        if self._last_local_pos is None:
            return False
        # NED z는 아래쪽 양수 → 고도 = -z.
        altitude_m = -self._last_local_pos.z
        return altitude_m >= (self.takeoff_altitude - self.altitude_tolerance)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OffboardControlNode()
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
