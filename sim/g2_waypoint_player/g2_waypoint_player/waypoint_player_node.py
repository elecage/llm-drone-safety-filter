"""G2 scripted waypoint player — YAML 시나리오 velocity·position 시퀀스 재생.

ADR-0011 D1 nominal 토픽 인터페이스에 시간축 따라 step별 명령을 publish:
  /cmd/trajectory_setpoint_nominal (velocity, TwistStamped)
  /cmd/pose_setpoint_nominal       (position, PoseStamped)

티어1 안전 필터(tier1_filter 패키지)가 nominal → safe 토픽으로 forward (B0)
또는 CBF-QP 변조 (B1·B2). 본 노드는 nominal 토픽에만 publish — *의도해석기*·
fault-injection·teleop 등 다른 nominal source와 swappable (ADR-0005 D3).

YAML 시나리오 스키마:
    name: <str>                    # 시나리오 식별자
    description: <str>             # 한 줄 설명
    publish_rate_hz: <float>       # publish 주파수 (기본 10)
    finish_hover_s: <float>        # 마지막 step 후 hover 유지 시간
    steps:
      - duration_s: <float>
        # type 옵션 (기본 "velocity"):
        type: velocity                                   # 또는 생략
        linear: {x: <float>, y: <float>, z: <float>}    # ENU m/s
        angular: {z: <float>}                            # ENU yaw rate rad/s (선택)
        note: <str>                                      # 디버깅용 (선택)
      - duration_s: <float>
        type: position
        position: {x: <float>, y: <float>, z: <float>}  # ENU 절대 위치 [m]
        yaw: <float>                                     # ENU yaw (선택, 기본 NaN=hold)
        note: <str>

position step의 의도: velocity 명령의 누적 drift를 corner anchor로 끊어 정확한
시퀀스 모양 보장 (예: C1 정사각형 닫힘, C2 dock 위 시작/종료).

시간 기준: wall time (Task #5 /clock 회복 전까지 sim time 미사용).
PX4 SITL은 RT factor ≈ 1.0이라 sim과 정합.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import rclpy
import yaml
from rclpy.node import Node

from geometry_msgs.msg import PoseStamped, TwistStamped


@dataclass
class Step:
    duration_s: float
    # 'velocity' 또는 'position' — 기본은 후방호환 velocity.
    type: str = 'velocity'
    # velocity step fields (ENU).
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    omega_z: float = 0.0
    # position step fields (ENU 절대 위치).
    px: float = 0.0
    py: float = 0.0
    pz: float = 0.0
    yaw: float = float('nan')   # NaN = G1이 현 yaw 유지
    note: str = ''


@dataclass
class Scenario:
    name: str
    description: str
    publish_rate_hz: float
    finish_hover_s: float
    steps: List[Step]


def load_scenario(path: Path) -> Scenario:
    with path.open('r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    steps = []
    for s in data.get('steps', []):
        stype = str(s.get('type', 'velocity')).lower()
        if stype not in ('velocity', 'position'):
            raise ValueError(f'step.type 무효: "{stype}" (velocity|position만 허용)')
        common = dict(
            duration_s=float(s['duration_s']),
            type=stype,
            note=str(s.get('note', '')),
        )
        if stype == 'position':
            pos = s.get('position', {}) or {}
            if not all(k in pos for k in ('x', 'y', 'z')):
                raise ValueError(f'position step에 position.x/y/z 필수: {s}')
            steps.append(Step(
                **common,
                px=float(pos['x']),
                py=float(pos['y']),
                pz=float(pos['z']),
                yaw=float(s.get('yaw', float('nan'))),
            ))
        else:
            linear = s.get('linear', {}) or {}
            angular = s.get('angular', {}) or {}
            steps.append(Step(
                **common,
                vx=float(linear.get('x', 0.0)),
                vy=float(linear.get('y', 0.0)),
                vz=float(linear.get('z', 0.0)),
                omega_z=float(angular.get('z', 0.0)),
            ))
    return Scenario(
        name=str(data.get('name', path.stem)),
        description=str(data.get('description', '')),
        publish_rate_hz=float(data.get('publish_rate_hz', 10.0)),
        finish_hover_s=float(data.get('finish_hover_s', 2.0)),
        steps=steps,
    )


class WaypointPlayerNode(Node):
    def __init__(self) -> None:
        super().__init__('g2_waypoint_player')

        self.declare_parameter('output_topic', '/cmd/trajectory_setpoint_nominal')
        self.declare_parameter('position_output_topic', '/cmd/pose_setpoint_nominal')
        self.declare_parameter('scenario_file', '')
        self.declare_parameter('frame_id', 'world')
        self.declare_parameter('exit_on_finish', True)

        output_topic = self.get_parameter('output_topic').value
        position_output_topic = self.get_parameter('position_output_topic').value
        scenario_path_str = self.get_parameter('scenario_file').value
        self.frame_id = self.get_parameter('frame_id').value
        self.exit_on_finish = bool(self.get_parameter('exit_on_finish').value)

        if not scenario_path_str:
            raise RuntimeError('scenario_file 파라미터 필수 (YAML 절대경로 또는 share 상대경로)')

        scenario_path = Path(scenario_path_str)
        if not scenario_path.is_absolute():
            # share/g2_waypoint_player/scenarios/ 에서 찾기.
            from ament_index_python.packages import get_package_share_directory
            share = Path(get_package_share_directory('g2_waypoint_player'))
            scenario_path = share / 'scenarios' / scenario_path_str
            if not scenario_path.suffix:
                scenario_path = scenario_path.with_suffix('.yaml')

        if not scenario_path.exists():
            raise RuntimeError(f'시나리오 파일 미발견: {scenario_path}')

        self.scenario = load_scenario(scenario_path)
        total_duration = sum(s.duration_s for s in self.scenario.steps) + self.scenario.finish_hover_s

        self.get_logger().info(
            f'G2 waypoint player 시작 — scenario="{self.scenario.name}", '
            f'steps={len(self.scenario.steps)}, total≈{total_duration:.1f}s, '
            f'rate={self.scenario.publish_rate_hz:.1f}Hz, output={output_topic}'
        )
        self.get_logger().info(f'    설명: {self.scenario.description}')

        self._pub = self.create_publisher(TwistStamped, output_topic, 10)
        self._pose_pub = self.create_publisher(PoseStamped, position_output_topic, 10)
        # 마지막으로 publish한 position 명령 — finish_hover 단계에서 재사용
        # (마지막 step이 position이면 그 위치 유지, velocity면 zero velocity).
        self._last_pos_cmd: Optional[Step] = None

        # 시간축 재생 — start_ns 기준 wall time. step 인덱스 관리.
        self._start_ns = self.get_clock().now().nanoseconds
        self._step_starts_s = []
        acc = 0.0
        for s in self.scenario.steps:
            self._step_starts_s.append(acc)
            acc += s.duration_s
        self._total_steps_end_s = acc
        self._session_end_s = acc + self.scenario.finish_hover_s

        self._last_logged_step: int = -1

        period = 1.0 / self.scenario.publish_rate_hz
        self._timer = self.create_timer(period, self._on_timer)

    def _on_timer(self) -> None:
        elapsed_s = (self.get_clock().now().nanoseconds - self._start_ns) * 1e-9

        if elapsed_s >= self._session_end_s:
            self._publish_finish()
            self.get_logger().info(
                f'[G2] 시나리오 "{self.scenario.name}" 완료 ({elapsed_s:.1f}s).'
            )
            if self.exit_on_finish:
                self.get_logger().info('[G2] exit_on_finish=True → 노드 종료.')
                rclpy.shutdown()
                return
            self._timer.cancel()
            return

        if elapsed_s >= self._total_steps_end_s:
            # 마지막 step 끝났으나 finish_hover 단계.
            # 마지막 step이 position이면 그 위치 hold, velocity면 zero velocity
            # (G1의 v≈0 → position hold 전이가 현 위치 캡처).
            self._publish_finish()
            return

        # 현재 step 인덱스 찾기.
        idx = 0
        for i, start_s in enumerate(self._step_starts_s):
            if elapsed_s >= start_s:
                idx = i
            else:
                break

        step = self.scenario.steps[idx]
        if idx != self._last_logged_step:
            note_str = f' — {step.note}' if step.note else ''
            if step.type == 'position':
                cmd_str = f'pos=({step.px:+.2f}, {step.py:+.2f}, {step.pz:+.2f})'
                if not math.isnan(step.yaw):
                    cmd_str += f', yaw={step.yaw:+.2f}'
            else:
                cmd_str = (
                    f'v=({step.vx:+.2f}, {step.vy:+.2f}, {step.vz:+.2f}), '
                    f'ω_z={step.omega_z:+.2f}'
                )
            self.get_logger().info(
                f'[G2] step {idx+1}/{len(self.scenario.steps)} '
                f'(t={elapsed_s:.1f}s, dur={step.duration_s:.1f}s) '
                f'{cmd_str}{note_str}'
            )
            self._last_logged_step = idx

        if step.type == 'position':
            self._publish_position(step.px, step.py, step.pz, step.yaw)
            self._last_pos_cmd = step
        else:
            self._publish_velocity(step.vx, step.vy, step.vz, step.omega_z)

    def _publish_finish(self) -> None:
        """finish_hover 또는 시나리오 종료 시 — 마지막 명령 type에 맞춰 hold."""
        if self._last_pos_cmd is not None:
            # 마지막이 position이었으면 그 위치 그대로 hold.
            p = self._last_pos_cmd
            self._publish_position(p.px, p.py, p.pz, p.yaw)
        else:
            # velocity 시나리오 → zero velocity. G1이 _hold_x/y_ned로 hold.
            self._publish_velocity(0.0, 0.0, 0.0, 0.0)

    def _publish_velocity(self, vx: float, vy: float, vz: float, omega_z: float) -> None:
        msg = TwistStamped()
        # header.stamp는 wall time (use_sim_time=False) — G1과 일관.
        now = self.get_clock().now().to_msg()
        msg.header.stamp = now
        msg.header.frame_id = self.frame_id
        msg.twist.linear.x = float(vx)
        msg.twist.linear.y = float(vy)
        msg.twist.linear.z = float(vz)
        msg.twist.angular.z = float(omega_z)
        self._pub.publish(msg)

    def _publish_position(self, px: float, py: float, pz: float, yaw: float) -> None:
        msg = PoseStamped()
        now = self.get_clock().now().to_msg()
        msg.header.stamp = now
        msg.header.frame_id = self.frame_id
        msg.pose.position.x = float(px)
        msg.pose.position.y = float(py)
        msg.pose.position.z = float(pz)
        # yaw는 quaternion z·w로 인코딩. NaN이면 0,0,0,1 (G1이 NaN 감지하려면
        # 별도 채널 필요 — 본 구현은 yaw NaN 표시를 단순화: w=1 → yaw=0이지만
        # G1은 PoseStamped.orientation.w가 0이면 "NaN 의도(yaw hold)"로 처리.)
        if math.isnan(yaw):
            # G1에 "yaw NaN" 신호 — quaternion 전부 0 (불완전 quaternion = NaN 의도).
            msg.pose.orientation.x = 0.0
            msg.pose.orientation.y = 0.0
            msg.pose.orientation.z = 0.0
            msg.pose.orientation.w = 0.0
        else:
            half = float(yaw) * 0.5
            msg.pose.orientation.x = 0.0
            msg.pose.orientation.y = 0.0
            msg.pose.orientation.z = math.sin(half)
            msg.pose.orientation.w = math.cos(half)
        self._pose_pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = WaypointPlayerNode()
    except Exception as e:
        print(f'[G2] 초기화 실패: {e}', file=__import__('sys').stderr)
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
