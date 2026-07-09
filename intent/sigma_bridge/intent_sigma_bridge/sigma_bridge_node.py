#!/usr/bin/env python3
"""sigma_bridge_node — /intent/llm_sigma_raw → /intent/target_waypoint

wrapper_node 출력 IntentResult JSON(sigma/theta/c)을 PoseStamped ENU 목표 지점으로
변환해 waypoint_follower 에 전달. waypoint_follower 가 목표 지점을 연속 속도로 바꿔
tier1 의 §5 정형 속도 CBF-QP 로 필터 → /cmd/trajectory_setpoint_safe → g1_offboard
→ PX4 (ADR-0029 D-A1 — 블로커 3 연속 속도 경로).

스킬 매핑:
  move_to       → (ADR-0027 amendment) theta.target_id(객체명) → scene world 좌표
                  결정론 lookup, 또는 theta.direction(토큰) → drone+오프셋. 좌표는
                  LLM 이 아니라 본 노드가 산출(작은 모델 좌표 환각 제거). 레거시
                  theta.position(world)도 호환. local ENU(spawn 보정) 변환 후 발행.
  inspect       → (ADR-0031) theta.target_class 후보 클러스터를 카메라에 담는
                  vantage pose 로 비행 + yaw 를 클러스터 중심으로 정렬. 도달까지
                  grounding gate 를 닫아(estimator s1 latch 보류) 도달 후 의미 있는
                  지각으로만 grounding 되게 한다. target_class 후보 부재 시 제자리
                  상승(레거시) fallback.
  return_to_dock→ ENU (0, 0, takeoff_altitude_m)
  emergency_land→ ENU (현재 x, 현재 y, 0.1)
  ask_user      → 로그만, 이동 없음

좌표 프레임 (ADR-0027 후속, C37b):
  scene/context 객체 좌표 = world frame(SDF). PX4 local frame = 드론 spawn 기준.
  target_id lookup·레거시 position(world)을 local = world − spawn 으로 변환 후
  publish (tier1 user_local 과 동일 frame 정합). spawn·scene 좌표는
  scenario_params single source. scenario_id 파라미터로 scenario 별 lookup.

사용자 회피 우회 (ADR-0028 amendment — sigma_bridge 책임 확장):
  drone→target 직선 segment 가 user 회피 영역과 교차하면 *수평 우회 waypoint*
  를 인터미디어트 setpoint 로 inject — drone 이 사용자 정면 saddle 에 멈추는
  CBF local minimum 문제 회피. 두 단계 큐 [우회_waypoint, 원_목표] 로 관리,
  drone 이 우회 waypoint 도달 시 (3D sphere 거리 < 임계) 다음 setpoint 발행.
  helper = intent_sigma_bridge.sigma_bridge_helpers.compute_detour_waypoint.
  ※ 운용 가드(회피 우회·standoff·z floor)는 데모 트랙(Track A) 기능 — 본실험
  live 경로는 user_guard_radius_m=0 으로 off 하고 tier1 r_max 가 단일 안전 책임을
  진다 (ADR-0028 Track B · ADR-0029 D-A1).

실행:
    ros2 launch intent_sigma_bridge sigma_bridge.launch.py scenario_id:=S5
"""

from __future__ import annotations

import json
import math
import os
import sys

import rclpy
from geometry_msgs.msg import PoseStamped
from px4_msgs.msg import VehicleLocalPosition
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import Bool, String

# helper 모듈 — pure 수학 (rclpy 무관). 패키지 내부 모듈 절대 import.
from intent_sigma_bridge.sigma_bridge_helpers import (
    apply_vertical_floor,
    candidate_cluster_center,
    compute_detour_waypoint,
    compute_radial_escape,
    compute_vantage_pose,
    direction_offset,
    distance_3d,
    has_arrived,
    inspect_referent_keys,
    is_segment_intersecting_sphere,
    lookup_object_position,
    wrap_angle,
    yaw_to_quaternion_zw,
)


def _px4_qos(depth: int = 10) -> QoSProfile:
    """PX4 micro-XRCE-DDS 토픽 QoS — BEST_EFFORT, VOLATILE, KEEP_LAST."""
    return QoSProfile(
        reliability=QoSReliabilityPolicy.BEST_EFFORT,
        durability=QoSDurabilityPolicy.VOLATILE,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=depth,
    )

# 방향 키워드 → ENU 상대 오프셋 (dx, dy, dz) [m]
# ENU 기준: x=East, y=North, z=Up
_DIRECTION_MAP: dict[str, tuple[float, float, float]] = {
    '앞': (0.0, 2.0, 0.0),
    '앞으로': (0.0, 2.0, 0.0),
    'forward': (0.0, 2.0, 0.0),
    '뒤': (0.0, -2.0, 0.0),
    '뒤로': (0.0, -2.0, 0.0),
    'back': (0.0, -2.0, 0.0),
    'backward': (0.0, -2.0, 0.0),
    '왼쪽': (-2.0, 0.0, 0.0),
    '왼': (-2.0, 0.0, 0.0),
    'left': (-2.0, 0.0, 0.0),
    '오른쪽': (2.0, 0.0, 0.0),
    '오른': (2.0, 0.0, 0.0),
    'right': (2.0, 0.0, 0.0),
    '위': (0.0, 0.0, 1.0),
    '위로': (0.0, 0.0, 1.0),
    '올라가': (0.0, 0.0, 1.0),
    'up': (0.0, 0.0, 1.0),
    '아래': (0.0, 0.0, -1.0),
    '아래로': (0.0, 0.0, -1.0),
    '내려가': (0.0, 0.0, -1.0),
    'down': (0.0, 0.0, -1.0),
}

_DEFAULT_TAKEOFF_ALT = 1.5
# move_to(절대 좌표) 시 target 으로부터 떨어질 standoff 거리 [m] — 객체(TV 등)
# 충돌 회피용 데모 가드. tier1/CBF 의 사용자 회피 영역과 별개로 *오브젝트* 충돌
# 회피. drone↔target 거리가 standoff 미만이면 비활성 (이미 가까이 있음).
# 0.7m: 가구 모델 형상(sofa/table 1m+ 길이) 대비 안전 마진.
_DEFAULT_TARGET_STANDOFF = 0.7
# 사용자 회피 영역 데모 운용 가드 반경 [m] — Track A 데모 기능.
# default = 0.0 (비활성): 본실험 live 경로(ADR-0029 D-A1)는 tier1 r_max 가 *단일*
# 안전 책임(ADR-0028 D2 Track B) — sigma_bridge 는 의도-제어 변환만 하고 회피는
# tier1 CBF 에 위임한다. 데모 트랙(Track A)에서만 launch/env param 으로 양수 지정
# (예: 1.0 = r_min 0.9 + 마진 0.1). 0 이면 _publish_pose_guarded 의 회피·우회·
# projection 분기가 모두 비활성(sphere 반경 0)이라 목표 지점을 그대로 통과시킨다.
_DEFAULT_USER_GUARD_RADIUS = 0.0
# 우회 waypoint 도달 임계 [m] — 3D sphere 거리. PX4 추종 잔여 오차 ≈ 0.45 m
# (STATUS C21 인접) + 마진 → 0.5 m. 작으면 우회 도달 못해 큐 stuck, 크면
# 우회 waypoint 통과 *전* 다음 목표로 전환되어 우회 효과 약화.
_DEFAULT_DETOUR_ARRIVAL_THRESHOLD = 0.5
# radial escape 후 user 와의 목표 xy 거리 비율 (= r_guard × 이 값). drone 이
# 회피 경계(예: "사용자에게 와" 직후 d_drone_user=r_guard)에 있어 단일 우회가
# 불가능할 때, 먼저 사용자에게서 이 거리만큼 멀어져 클리어런스를 확보한 뒤
# 정상 우회를 계산. compute_detour_waypoint 의 최소 클리어런스(1.2×r_guard)보다
# 커야 후속 우회가 성공하므로 1.3 (= 1.2 + 부동소수점 여유).
_ESCAPE_CLEARANCE_RATIO = 1.3
# inspect vantage standoff [m] — 클러스터 중심으로부터 수평 거리 (ADR-0031 기하
# 근거: altitude 1.5 m·의자 h_obj≈0.43 m 에서 하향각이 FOV 여유 구간 20°–45°에
# 들도록 1.5–2.0 m. 1.5 m → θ≈35.5° (FOV 안). 너무 작으면(0.7 m → θ≈57°) 프레임
# 아래로 빠짐(세션 47 현행 결함).
_DEFAULT_VANTAGE_STANDOFF = 1.5
# vantage 도달 임계 [m] — 3D sphere 거리. 도달 시 grounding gate 를 연다. PX4
# 추종 잔여(≈0.45 m) 보다 약간 크게 잡아 hover 안정 후 검출(과민 latch 방지).
_DEFAULT_VANTAGE_ARRIVAL_THRESHOLD = 0.5
# vantage yaw 정렬 임계 [rad] — 위치 도달 후 카메라 목표 yaw 와 현재 yaw 오차가 이
# 값 미만일 때만 grounding 개방. 15°(0.262 rad): 카메라 HFOV ~87°(half 43°) 대비
# 보수적(대상이 프레임 중앙 ±15° 안). 큰 yaw 회전(예 S6 sofa 137°)에서 위치만 도달
# 하고 yaw 미정렬인 채 grounding 되어 대상을 놓치는 것 방지 (ADR-0038 D2, 세션 58).
_DEFAULT_VANTAGE_YAW_THRESHOLD = 0.262
# ADR-0040 inspect 검색 스윕 — vantage 위치 도달 후 제자리 360° yaw 스윕(전방 고정
# 카메라가 referent 를 *지각으로* 포착, 결정론 yaw 조준 폐기). step = 목표 yaw 를
# 현재보다 항상 이만큼 앞(lead)에 둠 → follower P-제어가 saturated yawrate 로 연속
# 회전(스텝 대기 없이 빠른 한 바퀴). tol = 재발행 throttle [rad](목표가 이만큼
# 움직일 때만 pose 재발행 — 토픽·로그 폭주 방지).
_DEFAULT_VANTAGE_SWEEP_STEP = 0.5236   # π/6 = 30° lead → yaw 오차 포화 → max yawrate
_DEFAULT_VANTAGE_SWEEP_TOL = 0.15      # ~9° 재발행 throttle


class SigmaBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__('sigma_bridge')

        self.declare_parameter('output_waypoint_topic', '/intent/target_waypoint')
        self.declare_parameter('sigma_topic', '/intent/llm_sigma_raw')
        self.declare_parameter('utterance_topic', '/intent/user_prompt_raw')
        self.declare_parameter('default_move_dist_m', 2.0)
        self.declare_parameter('takeoff_altitude_m', _DEFAULT_TAKEOFF_ALT)
        # scenario_id (S5-S8) — move_to.position(world) → local 변환 spawn lookup.
        self.declare_parameter('scenario_id', 'S5')
        # ask_user 질문 발행 토픽 — host TTS(Piper)가 구독해 음성 출력 (ADR-0016 D3,
        # 명료화 루프 출력단). intent_tts.tts_bridge 정합.
        self.declare_parameter('ask_user_topic', '/intent/ask_user_question')
        self.declare_parameter('target_standoff_m', _DEFAULT_TARGET_STANDOFF)
        self.declare_parameter('user_guard_radius_m', _DEFAULT_USER_GUARD_RADIUS)
        self.declare_parameter(
            'detour_arrival_threshold_m', _DEFAULT_DETOUR_ARRIVAL_THRESHOLD,
        )
        # inspect vantage (ADR-0031) — 후보 클러스터를 카메라에 담는 standoff·도달
        # 임계 + grounding gate 토픽(도달 후 estimator s1 latch 개시 신호, D3).
        self.declare_parameter('vantage_standoff_m', _DEFAULT_VANTAGE_STANDOFF)
        self.declare_parameter(
            'vantage_arrival_threshold_m', _DEFAULT_VANTAGE_ARRIVAL_THRESHOLD,
        )
        self.declare_parameter(
            'vantage_yaw_threshold_rad', _DEFAULT_VANTAGE_YAW_THRESHOLD,
        )
        # ADR-0040 — vantage 도달 후 360° 검색 스윕 스텝·도달 tol [rad].
        self.declare_parameter(
            'vantage_sweep_step_rad', _DEFAULT_VANTAGE_SWEEP_STEP,
        )
        self.declare_parameter(
            'vantage_sweep_tol_rad', _DEFAULT_VANTAGE_SWEEP_TOL,
        )
        self.declare_parameter('grounding_gate_topic', '/intent/grounding_gate')

        out_pose = str(self.get_parameter('output_waypoint_topic').value)
        sigma_topic = str(self.get_parameter('sigma_topic').value)
        utterance_topic = str(self.get_parameter('utterance_topic').value)
        ask_user_topic = str(self.get_parameter('ask_user_topic').value)
        self._default_dist = float(self.get_parameter('default_move_dist_m').value)
        self._takeoff_alt = float(self.get_parameter('takeoff_altitude_m').value)
        self._standoff = float(self.get_parameter('target_standoff_m').value)
        self._user_guard_r = float(self.get_parameter('user_guard_radius_m').value)
        self._detour_threshold = float(
            self.get_parameter('detour_arrival_threshold_m').value
        )
        self._vantage_standoff = float(
            self.get_parameter('vantage_standoff_m').value
        )
        self._vantage_threshold = float(
            self.get_parameter('vantage_arrival_threshold_m').value
        )
        self._vantage_yaw_threshold = float(
            self.get_parameter('vantage_yaw_threshold_rad').value
        )
        self._sweep_step = float(
            self.get_parameter('vantage_sweep_step_rad').value
        )
        self._sweep_tol = float(
            self.get_parameter('vantage_sweep_tol_rad').value
        )
        gate_topic = str(self.get_parameter('grounding_gate_topic').value)
        scenario_id = str(self.get_parameter('scenario_id').value)

        # world → PX4 local frame 변환용 spawn offset (scenario_params single source).
        self._spawn_x, self._spawn_y, self._spawn_z = self._resolve_spawn(scenario_id)

        # 장면 객체 (name → world 좌표) — move_to.target_id 결정론 lookup
        # (ADR-0027 amendment). LLM 좌표 환각 제거. scenario_params.scene single source.
        self._scene_objects = self._resolve_scene_objects(scenario_id)

        # 사용자 회피 영역 (local ENU) + r_min — setpoint 침범 가드용 (이슈 2 회피).
        # tier1 자체 변경 대신 sigma_bridge 가 nominal 발행 전 *침범 시도 차단* (hover).
        (
            self._user_local_x,
            self._user_local_y,
            self._user_local_z,
            self._r_min,
        ) = self._resolve_user_local(scenario_id)

        # 현재 드론 위치 (ENU) — vehicle_local_position (NED)에서 변환
        self._enu_x: float = 0.0
        self._enu_y: float = 0.0
        self._enu_z: float = self._takeoff_alt
        self._enu_yaw: float = 0.0  # 드론 현재 yaw (ENU) — vantage yaw 정렬 판정용
        self._pos_valid: bool = False

        # 최근 utterance (move_to args 없을 때 방향 파싱용)
        self._last_utterance: str = ''

        # 우회 waypoint 큐 — [우회_waypoint, 원_목표] (compute_detour_waypoint
        # 결과 시 push). drone 도달 모니터링은 _on_local_pos. 큐 비면 normal.
        self._waypoint_queue: list[tuple[float, float, float]] = []

        # inspect vantage (ADR-0031) — 현 명령의 목표 yaw(전방 카메라 정렬용,
        # None=yaw 의도 없음=일반 이동) + vantage 도달 모니터 좌표 + grounding gate
        # 상태. gate 가 닫히면(False) estimator 가 s1 latch 를 보류해 vantage 도달
        # *후* 의 의미 있는 지각으로만 grounding 한다 (D3).
        self._pending_yaw: float | None = None
        self._vantage_target: tuple[float, float, float] | None = None
        self._grounding_open: bool = True
        # ADR-0040 — vantage 도달 후 360° 검색 스윕 상태. _sweep_active 동안 _on_local_pos
        # 가 _pending_yaw 를 스텝 전진시키며 pose 재발행(follower P-제어가 추종) →
        # 한 위치에서 yaw 만 회전(병진 0, tier1 위치 CBF 와 직교, D5).
        self._sweep_active: bool = False
        self._sweep_remaining: float = 0.0    # 남은 스윕 각 [rad]
        self._sweep_last_yaw: float = 0.0     # 직전 콜백 yaw (누적 회전 측정)
        self._sweep_pub_yaw: float = 0.0      # 마지막 발행 목표 yaw (재발행 throttle)

        self._pub_pose = self.create_publisher(PoseStamped, out_pose, 10)
        # grounding gate (Bool) — estimator 가 구독. 늦게 뜬 per-trial estimator 도
        # 마지막 상태를 받도록 transient_local(latching).
        self._pub_gate = self.create_publisher(
            Bool, gate_topic,
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=1,
            ),
        )
        # ask_user 질문 → host TTS (명료화 루프 출력단).
        self._pub_question = self.create_publisher(String, ask_user_topic, 10)
        # 실행 처분 음성 피드백 → host TTS (tts_bridge 구독). 의도 수락이 아니라
        # *실제 처분*(정상 이동 / 회피영역 projection / hover) 기반 — 안전 계층이
        # 막은 걸 사용자에게 음성으로 알린다 (통제권·투명성, ADR-0028 정합).
        self._pub_speech = self.create_publisher(String, '/intent/speech_out', 10)
        # 피드백 언어 — STT·TTS 공통 VOICE_LANG (start_intent_stack 가 forward).
        self._voice_ko = (os.environ.get('VOICE_LANG') or 'ko').strip().lower() != 'en'

        self.create_subscription(
            VehicleLocalPosition,
            '/fmu/out/vehicle_local_position_v1',
            self._on_local_pos,
            _px4_qos(),
        )
        self.create_subscription(String, utterance_topic, self._on_utterance, 10)
        self.create_subscription(String, sigma_topic, self._on_sigma, 10)

        self.get_logger().info(
            f'sigma_bridge 준비 — in={sigma_topic}  out={out_pose}  '
            f'scenario={scenario_id} spawn=({self._spawn_x},{self._spawn_y},{self._spawn_z})'
        )
        # 진단: 가드 값을 init 시점에 명시 — 재기동 후 즉시 적용 여부 확인용.
        self.get_logger().info(
            f'sigma_bridge guards: r_guard={self._user_guard_r:.2f}m '
            f'standoff={self._standoff:.2f}m z_floor={self._takeoff_alt:.2f}m '
            f'detour_arrival_th={self._detour_threshold:.2f}m'
        )
        self.get_logger().info(
            f'sigma_bridge user_local=({self._user_local_x:.2f},'
            f'{self._user_local_y:.2f},{self._user_local_z:.2f}) '
            f'r_min={self._r_min:.2f}m (tier1 정형)'
        )
        self.get_logger().info(
            f'sigma_bridge vantage: standoff={self._vantage_standoff:.2f}m '
            f'arrival_th={self._vantage_threshold:.2f}m gate={gate_topic}'
        )
        # 첫 명령 전 기본 open — inspect 전엔 estimator 가 즉시 grounding 가능.
        self._publish_gate(True)

    def _resolve_spawn(self, scenario_id: str) -> tuple[float, float, float]:
        """scenario_id → spawn world ENU (scenario_params). 실패 시 (0,0,0).

        scenario_params 미가용(빌드 안 됨)이거나 unknown scenario 시 spawn=(0,0,0)
        으로 fallback — world≡local 가정 (보정 없음). 경고 로깅.
        """
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

    def _resolve_scene_objects(self, scenario_id: str) -> list:
        """scenario_id → 장면 객체 list [{'name','position'(world)}] (scenario_params).

        move_to.target_id → world 좌표 결정론 lookup 용. 미가용 시 [] (lookup 전부
        실패 → ask_user fallback). LLM 좌표 직접 출력을 폐기하고 본 lookup 으로
        대체 (ADR-0027 amendment — 작은 모델 좌표 환각 제거).
        """
        try:
            from scenario_params.params import scenario_location
            from scenario_params.scene import scene_objects_for_location
            return scene_objects_for_location(scenario_location(scenario_id))
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(
                f'scene 객체 lookup 실패 (scenario={scenario_id!r}) → [] '
                f'(move_to.target_id 해석 불가, ask_user fallback): {exc}'
            )
            return []

    def _resolve_user_local(
        self, scenario_id: str
    ) -> tuple[float, float, float, float]:
        """scenario_id → user local ENU + r_min (scenario_params). 실패 시 (0,0,0,0.9)."""
        try:
            from scenario_params.params import scenario_location, tier1_local_params
            location = scenario_location(scenario_id)
            p = tier1_local_params(location)
            return (
                p['user_local_x'], p['user_local_y'], p['user_local_z'], p['r_min'],
            )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(
                f'user_local lookup 실패 (scenario={scenario_id!r}) → '
                f'(0,0,0) r_min=0.9 fallback (가드 effectively off): {exc}'
            )
            return 0.0, 0.0, 0.0, 0.9

    # ------------------------------------------------------------------
    def _on_local_pos(self, msg: VehicleLocalPosition) -> None:
        # NED → ENU: x_ENU = y_NED(East), y_ENU = x_NED(North), z_ENU = -z_NED
        self._enu_x = float(msg.y)
        self._enu_y = float(msg.x)
        self._enu_z = float(-msg.z)
        # PX4 heading (NED yaw, North 기준 [-π,π]) → ENU yaw (East 기준 CCW):
        # heading=0(North)→π/2, heading=π/2(East)→0. vantage yaw 정렬 판정용.
        self._enu_yaw = wrap_angle(math.pi / 2.0 - float(msg.heading))
        self._pos_valid = True
        self._check_waypoint_arrival()
        self._check_vantage_arrival()
        self._advance_sweep()

    def _check_waypoint_arrival(self) -> None:
        """우회 waypoint 큐 head 도달 시 pop + 다음 setpoint 발행.

        큐 비면 no-op. drone 위치 invalid 면 no-op (다음 _on_local_pos 콜백 대기).
        도달 임계 미만이면 1초마다 progress 로그 (큐 stuck 진단용).
        """
        if not self._waypoint_queue or not self._pos_valid:
            return
        current_target = self._waypoint_queue[0]
        drone_pos = (self._enu_x, self._enu_y, self._enu_z)
        d = distance_3d(drone_pos, current_target)

        # 큐 진행 모니터링 — 도달 못 한 동안 1초마다 거리 로그.
        # threshold 빡빡해 stuck 되는 케이스를 식별하기 위함.
        self.get_logger().info(
            f'[detour-progress] 큐 head=({current_target[0]:.2f},'
            f'{current_target[1]:.2f},{current_target[2]:.2f}) '
            f'drone=({drone_pos[0]:.2f},{drone_pos[1]:.2f},{drone_pos[2]:.2f}) '
            f'd={d:.2f}m threshold={self._detour_threshold:.2f}m '
            f'queue_len={len(self._waypoint_queue)}',
            throttle_duration_sec=1.0,
        )

        if d >= self._detour_threshold:
            return

        reached = self._waypoint_queue.pop(0)
        self.get_logger().info(
            f'[detour] waypoint ({reached[0]:.2f},{reached[1]:.2f},{reached[2]:.2f}) '
            f'도달 (d={d:.2f}m < threshold={self._detour_threshold} m)'
        )
        if self._waypoint_queue:
            next_target = self._waypoint_queue[0]
            self.get_logger().info(
                f'[detour] 다음 setpoint 발행 → '
                f'({next_target[0]:.2f},{next_target[1]:.2f},{next_target[2]:.2f})'
            )
            self._publish_pose(*next_target)

    def _on_utterance(self, msg: String) -> None:
        self._last_utterance = msg.data

    def _on_sigma(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, ValueError) as exc:
            self.get_logger().error(f'JSON 파싱 실패: {exc}')
            return

        sigma: str = data.get('sigma', '')
        theta: dict = data.get('theta', {})
        c: float = float(data.get('c', 0.0))

        self.get_logger().info(
            f'[sigma_bridge] σ={sigma!r}  c={c:.2f}  θ={theta}'
        )

        # 새 명령 — yaw 의도·vantage 모니터 리셋. inspect 만 vantage pose 로 비행하며
        # grounding gate 를 닫는다(도달까지 latch 보류, D3). 그 외 σ 는 즉시 grounding
        # 허용(gate open) — vantage 개념 없음.
        self._pending_yaw = None
        self._vantage_target = None
        self._sweep_active = False  # ADR-0040 — 새 명령은 진행 중 스윕 취소
        if sigma != 'inspect':
            self._set_grounding_gate(True)

        if sigma == 'move_to':
            disp = self._handle_move_to(theta)
            self._say_feedback(sigma, disp, theta)
        elif sigma == 'inspect':
            disp = self._handle_inspect(theta)
            self._say_feedback(sigma, disp, theta)
        elif sigma == 'return_to_dock':
            self._publish_pose_guarded(0.0, 0.0, self._takeoff_alt)
            self.get_logger().info('→ 도크 복귀 (0, 0, %.1f)' % self._takeoff_alt)
            self._say_feedback(sigma, 'normal', theta)
        elif sigma == 'emergency_land':
            # 비상 착륙은 회피 영역 가드 *우회* — 어디든 안전 최우선.
            self._publish_pose(self._enu_x, self._enu_y, 0.1)
            self.get_logger().warn('→ 비상 착륙 (가드 우회)')
            self._say_feedback(sigma, 'normal', theta)
        elif sigma == 'ask_user':
            q = theta.get('question', '(질문 없음)')
            self.get_logger().info(f'→ 사용자 확인 요청: {q!r}')
            # 명료화 루프 출력단 — host TTS(Piper)가 구독해 음성 출력.
            self._pub_question.publish(String(data=str(q)))
        else:
            self.get_logger().warn(f'알 수 없는 sigma: {sigma!r}')

    def _say_feedback(self, sigma: str, disp: str, theta: dict) -> None:
        """실제 처분(disp) + sigma 기반 음성 피드백을 /intent/speech_out 로 발행.

        disp: '_publish_pose_guarded' 가 반환한 처분 코드 —
          'normal'   정상 발행 (회피영역 밖, 그대로)
          'detour'   회피영역 우회 (돌아서 감)
          'projected' 회피영역 안 목표 → 경계로 밀어냄 (더 가까이 못 감)
          'hover'    우회 불가 → 현 위치 정지 (도달 불가)
          'as_is'    drone 위치 미수신 → 검사 없이 발행
          'unknown_target'  대상·방향 해석 불가 → 이동 없음 (확인 질문은
                     _handle_move_to 가 이미 발행 → 여기선 침묵).
        의도 수락이 아니라 *드론이 실제로 한 일* 을 알린다 (사용자 지적 정합).
        """
        ko = self._voice_ko
        target = str(theta.get('target_id', '')).strip()
        msg = ''
        if disp == 'unknown_target':
            # 확인 질문은 _handle_move_to 가 /intent/ask_user_question 으로 이미
            # 발행함 — 중복 음성 방지 위해 speech_out 침묵.
            return
        if disp in ('hover',):
            msg = ('사용자와 너무 가까워 그쪽으로는 갈 수 없어요.' if ko
                   else 'That is too close to you — I cannot go there.')
        elif disp in ('projected',):
            if sigma == 'inspect':
                msg = ('가까이는 못 가서 떨어진 곳에서 살펴볼게요.' if ko
                       else 'I will look from a distance — cannot get closer.')
            else:
                msg = ('더 가까이는 갈 수 없어 앞쪽에서 멈출게요.' if ko
                       else 'I will stop in front — cannot get closer.')
        else:  # normal / detour / as_is — 실행
            if sigma == 'move_to':
                base = '이동할게요.' if ko else 'Moving now.'
                msg = ('돌아서 ' + base) if disp == 'detour' and ko else (
                    ('Going around. ' + base) if disp == 'detour' else ('네, ' + base if ko else 'OK. ' + base))
            elif sigma == 'inspect':
                if ko:
                    msg = f'네, {target} 살펴볼게요.' if target else '네, 살펴볼게요.'
                else:
                    msg = f'OK, inspecting {target}.' if target else 'OK, inspecting.'
            elif sigma == 'return_to_dock':
                msg = '제자리로 돌아갈게요.' if ko else 'Returning to dock.'
            elif sigma == 'emergency_land':
                msg = '비상 착륙합니다.' if ko else 'Emergency landing.'
        if msg:
            self.get_logger().info(f'[speech_out] {msg}')
            self._pub_speech.publish(String(data=msg))

    # ------------------------------------------------------------------
    def _handle_move_to(self, theta: dict) -> str:
        """move_to 처리 → 처분 코드 반환 (_say_feedback 가 음성 결정).

        ADR-0027 amendment: LLM 은 *의미 선택* (target_id 객체명 / direction 토큰)
        만 출력하고, 좌표 산출은 본 결정론 핸들러가 담당. 작은 모델의 좌표 환각
        (Y 부호 뒤집힘·옆 객체 좌표) 을 구조적으로 제거. 우선순위:
          (A) target_id → scene 객체 world 좌표 lookup → local 변환 + standoff.
          (B) direction → drone 현재 위치 + 결정론 오프셋.
          (C) position → 구 백본 호환(레거시, world 좌표 직접 — ADR-0027 D2 이전).
          (D) 셋 다 없음 → utterance 방향 키워드 fallback (keyword 백본).
        """
        target = theta.get('target_id') or theta.get('target')
        direction = theta.get('direction')
        pos = theta.get('position')

        # (A) 명명 객체 — 결정론 좌표 lookup.
        if target:
            coord = lookup_object_position(target, self._scene_objects)
            if coord is None:
                self.get_logger().warn(
                    f'→ move_to target_id={target!r} 장면에 없음 — 이동 보류, 확인 요청'
                )
                q = (f'"{target}"을(를) 찾지 못했어요. 어디로 갈까요?'
                     if self._voice_ko
                     else f'I could not find "{target}". Where should I go?')
                self._pub_question.publish(String(data=q))
                return 'unknown_target'
            wx, wy, wz = coord
            x = wx - self._spawn_x
            y = wy - self._spawn_y
            z = wz - self._spawn_z
            self.get_logger().info(
                f'→ move_to target_id={target!r} world=({wx:.2f},{wy:.2f},{wz:.2f}) '
                f'→ local ENU=({x:.2f},{y:.2f},{z:.2f})'
            )
            # 객체 standoff — drone↔target 벡터의 target 측 standoff 만큼 뒤로
            # 물러난 점을 setpoint 으로 (TV/가구 충돌 데모 가드).
            x, y, z = self._apply_standoff(x, y, z)
            return self._publish_pose_guarded(x, y, z)

        # (B) 상대 방향 — drone 현재 위치 + 결정론 오프셋.
        if direction:
            off = direction_offset(direction)
            if off is None:
                self.get_logger().warn(
                    f'→ move_to direction={direction!r} 무효 — 이동 보류, 확인 요청'
                )
                q = ('어느 방향으로 갈까요? (앞/뒤/왼쪽/오른쪽/위/아래)'
                     if self._voice_ko
                     else 'Which direction? (forward/back/left/right/up/down)')
                self._pub_question.publish(String(data=q))
                return 'unknown_target'
            dx, dy, dz = off
            tx = self._enu_x + dx
            ty = self._enu_y + dy
            tz = max(0.5, self._enu_z + dz)  # 최소 0.5m 고도 유지
            self.get_logger().info(
                f'→ move_to direction={direction!r} Δ=({dx},{dy},{dz}) '
                f'→ ENU=({tx:.2f},{ty:.2f},{tz:.2f})'
            )
            return self._publish_pose_guarded(tx, ty, tz)

        # (C) 레거시 position — world 좌표 직접 (구 백본 호환, ADR-0027 D2 이전).
        if pos and len(pos) >= 3:
            wx, wy, wz = float(pos[0]), float(pos[1]), float(pos[2])
            x = wx - self._spawn_x
            y = wy - self._spawn_y
            z = wz - self._spawn_z
            self.get_logger().info(
                f'→ move_to (레거시 position) world=({wx:.2f},{wy:.2f},{wz:.2f}) '
                f'→ local ENU=({x:.2f},{y:.2f},{z:.2f})'
            )
            x, y, z = self._apply_standoff(x, y, z)
            return self._publish_pose_guarded(x, y, z)

        # (D) 셋 다 없음 — utterance 방향 키워드 fallback (keyword 백본).
        dx, dy, dz = self._parse_direction(self._last_utterance)
        tx = self._enu_x + dx
        ty = self._enu_y + dy
        tz = max(0.5, self._enu_z + dz)
        self.get_logger().info(
            f'→ move_to 상대 이동(utterance) Δ=({dx},{dy},{dz}) '
            f'→ ENU=({tx:.2f},{ty:.2f},{tz:.2f})'
        )
        return self._publish_pose_guarded(tx, ty, tz)

    # ------------------------------------------------------------------
    # inspect vantage (ADR-0031)
    # ------------------------------------------------------------------
    def _handle_inspect(self, theta: dict) -> str:
        """inspect → 지시 클래스 후보 클러스터 vantage 비행 (ADR-0031).

        ``theta.target_class``(OVD 클래스, wrapper 가 주입 — ADR-0029 블로커 1)에
        해당하는 scene 객체 전체의 중심을 향해 vantage pose(standoff·고도·yaw)를
        잡아 비행한다. 동일 클래스 후보를 한 프레임에 담아 모호성을 보존(C2, D2).
        도달까지 grounding gate 를 닫아 estimator 의 s1 latch 를 보류한다(D3).

        ``target_class`` 부재(키워드/구 백본)·scene 후보 부재 시 제자리 상승
        (레거시 +0.5 m)으로 fallback 하고 gate 를 연다(즉시 grounding).
        """
        # 지시 클래스(OVD 라벨) 후보 키 집합 — 백본별 σ 형식 차이 흡수
        # (target_class·target_id·토큰 분해·인스턴스 name lookup). direct mode 합성
        # 라벨('mug_cup'→'cup') 토큰 흡수 포함 (inspect_referent_keys, 단위 테스트).
        keys = inspect_referent_keys(theta, self._scene_objects)
        candidates = [
            obj['position'] for obj in self._scene_objects
            if obj.get('ovd_class')
            and str(obj['ovd_class']).strip().lower() in keys
        ]
        center_world = candidate_cluster_center(candidates)
        if center_world is None:
            self.get_logger().warn(
                f'→ inspect 후보 클러스터 없음 (keys={sorted(keys)}, θ={theta}) '
                f'→ 제자리 상승(+0.5m) fallback, grounding 즉시 개시'
            )
            self._set_grounding_gate(True)
            return self._publish_pose_guarded(
                self._enu_x, self._enu_y, self._enu_z + 0.5
            )

        # world → local ENU (spawn 보정, move_to 와 동일 규약).
        cx = center_world[0] - self._spawn_x
        cy = center_world[1] - self._spawn_y
        cz = center_world[2] - self._spawn_z
        drone = (self._enu_x, self._enu_y, self._enu_z)
        (vx, vy, vz), yaw = compute_vantage_pose(
            (cx, cy, cz), drone, self._vantage_standoff, self._takeoff_alt,
        )
        # 목표 yaw(전방 카메라 정렬) — _publish_pose 가 orientation 에 인코딩,
        # follower 가 yawspeed 로 추종. vantage 도달 좌표는 도달 모니터에 등록.
        self._pending_yaw = yaw
        self._vantage_target = (vx, vy, vz)
        # 도달까지 grounding 보류 (D3 — vantage 도달 전 빈 s1 freeze 차단).
        self._set_grounding_gate(False)
        self.get_logger().info(
            f'→ inspect vantage: keys={sorted(keys)} 후보={len(candidates)}개 '
            f'center_local=({cx:.2f},{cy:.2f},{cz:.2f}) '
            f'vantage=({vx:.2f},{vy:.2f},{vz:.2f}) yaw={math.degrees(yaw):.0f}° '
            f'(grounding gate 닫음 — 도달 대기)'
        )
        return self._publish_pose_guarded(vx, vy, vz)

    def _check_vantage_arrival(self) -> None:
        """vantage 도달 시 grounding gate 를 연다 (ADR-0031 D3).

        inspect vantage 비행 중(``_vantage_target`` 설정 + gate 닫힘)에만 동작.
        도달(3D sphere 거리 < 임계) 시 gate 를 열어 estimator 가 그 시점부터
        s1 latch 를 개시하게 한다 — 도달 전 OVD 스침으로 빈 s1 이 동결되는 것을
        막는다(blocker 2a latch 와 협조).
        """
        if (
            self._vantage_target is None
            or self._grounding_open
            or not self._pos_valid
        ):
            return
        d = distance_3d(
            (self._enu_x, self._enu_y, self._enu_z), self._vantage_target
        )
        if d >= self._vantage_threshold:
            return  # 위치 미도달

        # ADR-0040 — vantage *영역* 위치 도달(yaw 무관)로 grounding gate 를 열고
        # 360° 검색 스윕을 개시한다. 종전(ADR-0038/PR#286)의 "referent 방위로 yaw
        # 정렬까지 대기" 게이트는 폐기 — 정착 yaw 가 run-to-run 비결정적이고
        # ground-truth 방위에 의존(sim 편법)했다. 스윕은 한 위치에서 yaw 만 돌려
        # 카메라가 referent 를 지각으로 포착하게 한다(방위 사전지식 불요).
        self.get_logger().info(
            f'[vantage] 영역 도달 (d={d:.2f}m) → grounding gate 개방 + 360° 검색 스윕 개시'
        )
        self._set_grounding_gate(True)
        self._start_sweep()

    def _set_grounding_gate(self, is_open: bool) -> None:
        """grounding gate 상태 갱신 + 발행 (멱등 — 상태 동일 시 재발행 skip)."""
        if is_open == self._grounding_open:
            return
        self._grounding_open = is_open
        self._publish_gate(is_open)

    def _start_sweep(self) -> None:
        """ADR-0040 — vantage 도달 후 360° 검색 스윕 개시 (연속 lead).

        목표 yaw 를 현재 yaw 보다 한 lead(``_sweep_step``) 앞에 둔다. follower
        P-제어가 항상 큰 오차를 받아 ~yawrate_max 로 회전 → 빠른 연속 스윕(스텝
        대기 없음). ``_advance_sweep`` 이 누적 회전을 추적해 2π 에 종료.
        병진은 vantage 위치 고정(yaw 만 회전) — tier1 위치 CBF 와 직교(D5).
        """
        if self._vantage_target is None:
            return
        self._sweep_active = True
        self._sweep_remaining = 2.0 * math.pi
        self._sweep_last_yaw = self._enu_yaw
        self._pending_yaw = wrap_angle(self._enu_yaw + self._sweep_step)
        self._sweep_pub_yaw = self._pending_yaw
        vx, vy, vz = self._vantage_target
        self._publish_pose(vx, vy, vz)

    def _advance_sweep(self) -> None:
        """스윕 중 목표 yaw 를 현재보다 lead 앞에 유지 + 누적 회전 2π 시 종료.

        ``_on_local_pos`` 가 매 위치 갱신마다 호출. 직전 콜백 대비 회전량을
        누적(``_sweep_remaining`` 차감)하고, 2π 도달 시 종료(최종 yaw 유지).
        아니면 목표 yaw 를 현재+lead 로 갱신; 재발행은 목표가 ``_sweep_tol``
        이상 움직였을 때만(throttle — 로그·토픽 폭주 방지).
        """
        if not self._sweep_active or self._vantage_target is None or not self._pos_valid:
            return
        self._sweep_remaining -= abs(wrap_angle(self._enu_yaw - self._sweep_last_yaw))
        self._sweep_last_yaw = self._enu_yaw
        if self._sweep_remaining <= 0.0:
            self._sweep_active = False
            self.get_logger().info('[vantage] 360° 검색 스윕 완료 — 최종 yaw 유지')
            return
        target = wrap_angle(self._enu_yaw + self._sweep_step)
        self._pending_yaw = target
        if abs(wrap_angle(target - self._sweep_pub_yaw)) >= self._sweep_tol:
            self._sweep_pub_yaw = target
            vx, vy, vz = self._vantage_target
            self._publish_pose(vx, vy, vz)

    def _publish_gate(self, is_open: bool) -> None:
        self._pub_gate.publish(Bool(data=bool(is_open)))

    def _apply_standoff(
        self, tx: float, ty: float, tz: float
    ) -> tuple[float, float, float]:
        """target 으로부터 standoff [m] 떨어진 setpoint 계산.

        drone↔target 거리 > standoff 일 때만 적용 (target 이 이미 가까우면 그대로).
        drone 위치 invalid (PX4 미수신) 시 그대로 (standoff 의미 없음).
        """
        if not self._pos_valid or self._standoff <= 0.0:
            return tx, ty, tz
        dx = tx - self._enu_x
        dy = ty - self._enu_y
        dz = tz - self._enu_z
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist <= self._standoff:
            return tx, ty, tz
        scale = (dist - self._standoff) / dist
        sx = self._enu_x + dx * scale
        sy = self._enu_y + dy * scale
        sz = self._enu_z + dz * scale
        self.get_logger().info(
            f'  standoff {self._standoff:.2f}m 적용 → '
            f'ENU=({sx:.2f},{sy:.2f},{sz:.2f})'
        )
        return sx, sy, sz

    def _publish_pose_guarded(self, x: float, y: float, z: float) -> str:
        """사용자 회피 영역 + 수직 floor 침범 가드. 처분 코드 반환
        ('normal'|'detour'|'projected'|'hover'|'as_is') — _say_feedback 가 음성 결정.

        분기:
          (0) z floor 강제 — setpoint z < takeoff_altitude_m 이면 takeoff_alt
              로 올림. emergency_land 는 본 함수 우회.
          (1) setpoint 자체가 r_guard 안 → *drone↔user 직선의 drone 쪽 r_guard
              외곽점* 으로 projection.
          (2a) drone→setpoint 직선 segment 가 r_guard 와 교차 + 우회 가능 →
              *수평 우회 waypoint* 인터미디어트 setpoint 큐에 push, 우회 먼저
              발행. drone 도달 시 _on_local_pos → 큐 pop + 원 목표 발행.
          (2a-escape) segment 교차 + drone 이 회피 경계에 너무 가까워(예:
              "사용자에게 와" 직후 d_drone_user=r_guard) 단일 우회 불가 +
              goal 은 회피 영역 밖 → *사용자에게서 먼저 멀어지는 radial escape
              waypoint*(항상 안전)로 클리어런스 확보 후 우회. 큐 [escape,
              (detour), goal]. drone 이 사용자 곁에 한 번 가도 갇히지 않게 함.
          (2b) segment 교차 + 우회·탈출 모두 *불가능* (goal 도 회피 영역
              근접 등) → drone 위치 hover + warn. 사용자 보호 우선 (paper §C
              narrative 정합). drone 이 사용자 회피 영역으로 직선 통과 시도
              차단. 목표 도달 불가는 호출측·상위 계층에 명시.
          (3) segment 자체가 안전 → 그대로 publish.

        drone 위치 미수신 시 segment 검사 불가 → 그대로 publish (분기 (3))
        + warn. 새 setpoint 받을 때마다 기존 큐 비움 (의도 변경 = 우회 무효화).
        """
        # 새 명령 → 기존 큐 무효화 (직전 우회 미완료라도 새 의도 우선).
        self._waypoint_queue.clear()

        # 진단 헤더 — 매 호출마다 핵심 변수 한 줄 (분기 선택 추적).
        drone_xyz = (self._enu_x, self._enu_y, self._enu_z)
        user_xyz = (
            self._user_local_x, self._user_local_y, self._user_local_z,
        )
        self.get_logger().info(
            f'[guard] target=({x:.2f},{y:.2f},{z:.2f}) '
            f'drone=({drone_xyz[0]:.2f},{drone_xyz[1]:.2f},{drone_xyz[2]:.2f}) '
            f'user=({user_xyz[0]:.2f},{user_xyz[1]:.2f},{user_xyz[2]:.2f}) '
            f'r_guard={self._user_guard_r:.2f} pos_valid={self._pos_valid}'
        )

        # 분기 (0): z floor — 가구·바닥 충돌 회피.
        z_floored = apply_vertical_floor(z, self._takeoff_alt)
        if z_floored != z:
            self.get_logger().info(
                f'[guard] (0) z floor: z={z:.2f} → {z_floored:.2f} '
                f'(< takeoff_alt={self._takeoff_alt:.2f})'
            )
            z = z_floored

        # 분기 (1): setpoint 자체 r_guard 침범
        dux = x - self._user_local_x
        duy = y - self._user_local_y
        duz = z - self._user_local_z
        d_user_target = math.sqrt(dux * dux + duy * duy + duz * duz)
        if d_user_target < self._user_guard_r:
            self.get_logger().info(
                f'[guard] (1) target inside r_guard '
                f'(d_user_target={d_user_target:.2f} < {self._user_guard_r:.2f}) '
                f'→ radial projection'
            )
            return self._project_setpoint_radial(x, y, z, d_user_target)

        # 분기 (2)/(3) — drone 위치 유효 시 segment 검사 우선.
        if not self._pos_valid:
            self.get_logger().warn(
                f'[guard] (2/3) drone pos invalid (PX4 미수신) → '
                f'segment 검사 skip, publish ({x:.2f},{y:.2f},{z:.2f}) as-is'
            )
            self._publish_pose(x, y, z)
            return 'as_is'

        # 분기 (3): segment 안전 — 그대로 publish.
        if not is_segment_intersecting_sphere(
            seg_a=drone_xyz, seg_b=(x, y, z),
            sphere_center=user_xyz, sphere_radius=self._user_guard_r,
        ):
            self.get_logger().info(
                f'[guard] (3) segment 안전 → publish ({x:.2f},{y:.2f},{z:.2f}) as-is'
            )
            self._publish_pose(x, y, z)
            return 'normal'

        # segment 위반 — detour 시도.
        d_drone_user_xy = math.sqrt(
            (drone_xyz[0] - user_xyz[0]) ** 2
            + (drone_xyz[1] - user_xyz[1]) ** 2
        )
        d_goal_user_xy = math.sqrt(
            (x - user_xyz[0]) ** 2 + (y - user_xyz[1]) ** 2
        )
        min_clearance = 1.2 * self._user_guard_r

        detour = compute_detour_waypoint(
            drone=drone_xyz,
            goal=(x, y, z),
            user=user_xyz,
            r_guard=self._user_guard_r,
        )
        if detour is not None:
            # 분기 (2a): segment 교차 + 우회 가능.
            self._waypoint_queue = [detour, (x, y, z)]
            self.get_logger().info(
                f'[guard] (2a) segment 교차 + detour inject: '
                f'waypoint=({detour[0]:.2f},{detour[1]:.2f},{detour[2]:.2f}) '
                f'→ original=({x:.2f},{y:.2f},{z:.2f}) '
                f'(d_drone_user_xy={d_drone_user_xy:.2f}, '
                f'd_goal_user_xy={d_goal_user_xy:.2f})'
            )
            self._publish_pose(*detour)
            return 'detour'

        # 분기 (2a-escape): drone 이 회피 경계에 너무 가까워 단일 우회가 불가능
        # (d_drone_user_xy < 1.2·r_guard — 예: "사용자에게 와" 직후 경계 주차).
        # 사용자에게서 *먼저 멀어지는* 탈출 waypoint(항상 안전)로 클리어런스를
        # 확보한 뒤 정상 우회를 계산 → 큐 [escape, (detour), goal]. goal 자체는
        # 회피 영역 밖(d_goal_user_xy ≥ min_clearance)이어야 의미 있음.
        if d_drone_user_xy < min_clearance and d_goal_user_xy >= min_clearance:
            escape = compute_radial_escape(
                drone_xyz, user_xyz, self._user_guard_r,
                target_clearance=_ESCAPE_CLEARANCE_RATIO * self._user_guard_r,
            )
            if escape is not None:
                # 탈출 후 직선이 이미 안전하면 [escape, goal],
                # 아니면 escape 기준 단일 우회 w 를 추가 [escape, w, goal].
                if not is_segment_intersecting_sphere(
                    seg_a=escape, seg_b=(x, y, z),
                    sphere_center=user_xyz, sphere_radius=self._user_guard_r,
                ):
                    self._waypoint_queue = [escape, (x, y, z)]
                else:
                    w = compute_detour_waypoint(
                        drone=escape, goal=(x, y, z),
                        user=user_xyz, r_guard=self._user_guard_r,
                    )
                    self._waypoint_queue = (
                        [escape, w, (x, y, z)] if w is not None else []
                    )
                if self._waypoint_queue:
                    self.get_logger().info(
                        f'[guard] (2a-escape) drone 경계 주차 → radial escape '
                        f'({escape[0]:.2f},{escape[1]:.2f},{escape[2]:.2f}) 후 '
                        f'우회: 큐={[(round(p[0],2),round(p[1],2)) for p in self._waypoint_queue]} '
                        f'(d_drone_user_xy={d_drone_user_xy:.2f} < '
                        f'min_clearance={min_clearance:.2f})'
                    )
                    self._publish_pose(*escape)
                    return 'detour'

        # 분기 (2b): segment 교차 + 단일 waypoint 우회 불가능 → drone 위치 hover.
        # 사용자 보호 우선 (paper §C narrative — 사용자 회피 영역으로의 직선
        # 통과 시도 차단). 목표 도달 불가는 운용 한계로 명시.
        self.get_logger().warn(
            f'[guard] (2b) segment 회피 영역 침범 + 단일 waypoint 우회 불가 '
            f'(d_drone_user_xy={d_drone_user_xy:.2f}, '
            f'd_goal_user_xy={d_goal_user_xy:.2f}, '
            f'min_clearance={min_clearance:.2f}) — 목표 ({x:.2f},{y:.2f},{z:.2f}) '
            f'도달 불가. drone 위치 hover (사용자 보호 우선).'
        )
        self._publish_pose(self._enu_x, self._enu_y, self._enu_z)
        return 'hover'

    def _project_setpoint_radial(
        self, x: float, y: float, z: float, d_user_target: float,
    ) -> str:
        """setpoint 가 r_guard 안일 때 drone↔user 직선의 drone 쪽 외곽점 projection.
        처분 코드 반환 ('projected'|'hover'|'as_is').

        drone 위치 미수신 또는 drone≈user 케이스는 안전하게 현 위치 hover.
        """
        if not self._pos_valid:
            self.get_logger().warn(
                f'  → setpoint ({x:.2f},{y:.2f},{z:.2f}) 회피 영역 안이나 '
                f'drone pos 미수신 — skip'
            )
            return 'as_is'

        ddx = self._user_local_x - self._enu_x
        ddy = self._user_local_y - self._enu_y
        ddz = self._user_local_z - self._enu_z
        d_drone_user = math.sqrt(ddx * ddx + ddy * ddy + ddz * ddz)
        if d_drone_user < 1e-6:
            self.get_logger().warn('  → drone≈user 위치 — 현 위치 hover')
            self._publish_pose(self._enu_x, self._enu_y, self._enu_z)
            return 'hover'

        scale = self._user_guard_r / d_drone_user
        px = self._user_local_x - ddx * scale
        py = self._user_local_y - ddy * scale
        pz = self._user_local_z - ddz * scale
        self.get_logger().info(
            f'  → setpoint ({x:.2f},{y:.2f},{z:.2f}) 회피 영역 안 '
            f'(d_user={d_user_target:.2f} < guard={self._user_guard_r}) → '
            f'r={self._user_guard_r}m 경계로 projection ({px:.2f},{py:.2f},{pz:.2f})'
        )
        self._publish_pose(px, py, pz)
        return 'projected'

    def _parse_direction(self, utterance: str) -> tuple[float, float, float]:
        text = utterance.lower()
        # 키 길이 내림차순 — 긴 키워드("앞으로", "왼쪽")가 짧은 키워드("앞",
        # "왼")보다 먼저 매치되도록(현 맵엔 의미 충돌 없으나 향후 추가에 견고).
        for kw, offset in sorted(_DIRECTION_MAP.items(), key=lambda kv: -len(kv[0])):
            if kw.lower() in text:
                self.get_logger().info(f'방향 키워드 "{kw}" 감지')
                return offset
        self.get_logger().warn(f'방향 키워드 없음 ("{utterance[:30]}") — 현재 위치 유지')
        return 0.0, 0.0, 0.0

    def _publish_pose(self, x: float, y: float, z: float) -> None:
        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'map'
        msg.pose.position.x = x
        msg.pose.position.y = y
        msg.pose.position.z = z
        # 목표 yaw — inspect vantage 만 설정(전방 카메라 정렬). None 이면 all-zero
        # quaternion(yaw 의도 없음) → follower/g1 이 yaw 제어 skip(일반 이동 보존).
        if self._pending_yaw is None:
            msg.pose.orientation.x = 0.0
            msg.pose.orientation.y = 0.0
            msg.pose.orientation.z = 0.0
            msg.pose.orientation.w = 0.0
        else:
            qz, qw = yaw_to_quaternion_zw(self._pending_yaw)
            msg.pose.orientation.z = qz
            msg.pose.orientation.w = qw
        self._pub_pose.publish(msg)
        yaw_str = (
            'none' if self._pending_yaw is None
            else f'{math.degrees(self._pending_yaw):.0f}°'
        )
        self.get_logger().info(
            f'[sigma_bridge] ✓ pose_setpoint_nominal 발행: '
            f'ENU=({x:.2f}, {y:.2f}, {z:.2f}) yaw={yaw_str}'
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = SigmaBridgeNode()
    except Exception as exc:
        print(f'[sigma_bridge] init 실패: {exc}', file=sys.stderr)
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
