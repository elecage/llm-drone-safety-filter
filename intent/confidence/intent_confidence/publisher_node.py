"""Mock 의도해석기 — confidence channel publisher.

YAML segment (constant / ramp / step) 기반 시간축 재생으로
/intent/grounding_confidence (std_msgs/Float32, c ∈ [0,1]) publish.

Phase 2b B2 검증 3종을 cover:
  1. c_constant_1.yaml  — 상수 c=1.0 (B1 regression)
  2. c_constant_0.yaml  — 상수 c=0.0 (r=r_max brake)
  3. c_step_down.yaml   — t=5s에서 1→0 step (변화율 제한기 발동)
  4. c_ramp_down.yaml   — 10s 동안 1→0 선형 ramp (변화율 제한기 미발동 vs 발동 경계)

raw c를 그대로 publish; tier1_filter의 변화율 제한기가 $\\tilde c$로 변환.
본 노드는 *의도해석기*-class 인터페이스 reference (ADR-0005 D3) — 향후 LLM/VLA
백본으로 swap 시 본 노드를 대체 (toscho 같은 토픽·QoS·메시지 형식 유지).

YAML 스키마:
    name: <str>
    description: <str>
    publish_rate_hz: <float>     # 기본 10
    finish_hover_s: <float>      # 기본 2
    segments:
      - duration_s: <float>
        type: constant | ramp | step
        # constant:
        value: <float>           # [0, 1]
        # ramp:
        from: <float>            # [0, 1] (segment 시작 값)
        to: <float>              # [0, 1] (segment 끝 값)
        # step (즉시 점프 후 끝까지 hold):
        value: <float>           # [0, 1]
        note: <str>              # 디버깅용 (선택)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import rclpy
import yaml
from rclpy.node import Node

from std_msgs.msg import Float32


@dataclass
class Segment:
    duration_s: float
    type: str  # 'constant' | 'ramp' | 'step' | 'stall'
    value: float = 0.0       # constant·step에서 사용
    val_from: float = 0.0    # ramp 시작
    val_to: float = 0.0      # ramp 끝
    note: str = ''
    # stall: 구간 동안 *발행 자체를 억제* — LLM 지연 급증/무응답 모사(ADR-0050 D3,
    # RQ3 지연 독립성). 이때 tier1 은 마지막 유효 신뢰도로 매 주기 동작해야 한다.


@dataclass
class Scenario:
    name: str
    description: str
    publish_rate_hz: float
    finish_hover_s: float
    segments: List[Segment]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def load_scenario(path: Path) -> Scenario:
    with path.open('r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    segments = []
    for s in data.get('segments', []):
        stype = str(s.get('type', 'constant')).lower()
        if stype not in ('constant', 'ramp', 'step', 'stall'):
            raise ValueError(
                f'segment.type 무효: "{stype}" (constant|ramp|step|stall만 허용)'
            )
        common = dict(
            duration_s=float(s['duration_s']),
            type=stype,
            note=str(s.get('note', '')),
        )
        if stype == 'ramp':
            if 'from' not in s or 'to' not in s:
                raise ValueError(f'ramp segment에 from·to 필수: {s}')
            segments.append(Segment(
                **common,
                val_from=_clamp01(float(s['from'])),
                val_to=_clamp01(float(s['to'])),
            ))
        elif stype == 'stall':
            # 발행 억제 구간 — value 불필요(무발행).
            segments.append(Segment(**common))
        else:
            if 'value' not in s:
                raise ValueError(f'{stype} segment에 value 필수: {s}')
            segments.append(Segment(**common, value=_clamp01(float(s['value']))))
    return Scenario(
        name=str(data.get('name', path.stem)),
        description=str(data.get('description', '')),
        publish_rate_hz=float(data.get('publish_rate_hz', 10.0)),
        finish_hover_s=float(data.get('finish_hover_s', 2.0)),
        segments=segments,
    )


class ConfidencePublisherNode(Node):
    def __init__(self) -> None:
        super().__init__('intent_confidence_publisher')

        self.declare_parameter('output_topic', '/intent/grounding_confidence')
        self.declare_parameter('scenario_file', '')
        self.declare_parameter('exit_on_finish', False)
        # finish_hover 동안 마지막 segment의 *끝 값*을 hold (구체 값은 시나리오에 따름).

        output_topic = self.get_parameter('output_topic').value
        scenario_path_str = self.get_parameter('scenario_file').value
        self.exit_on_finish = bool(self.get_parameter('exit_on_finish').value)

        if not scenario_path_str:
            raise RuntimeError(
                'scenario_file 파라미터 필수 (YAML 절대경로 또는 share 상대경로)'
            )

        scenario_path = Path(scenario_path_str)
        if not scenario_path.is_absolute():
            from ament_index_python.packages import get_package_share_directory
            share = Path(get_package_share_directory('intent_confidence'))
            scenario_path = share / 'scenarios' / scenario_path_str
            if not scenario_path.suffix:
                scenario_path = scenario_path.with_suffix('.yaml')

        if not scenario_path.exists():
            raise RuntimeError(f'시나리오 파일 미발견: {scenario_path}')

        self.scenario = load_scenario(scenario_path)
        total_duration = sum(s.duration_s for s in self.scenario.segments) + self.scenario.finish_hover_s

        self.get_logger().info(
            f'intent_confidence publisher 시작 — scenario="{self.scenario.name}", '
            f'segments={len(self.scenario.segments)}, total≈{total_duration:.1f}s, '
            f'rate={self.scenario.publish_rate_hz:.1f}Hz, output={output_topic}'
        )
        self.get_logger().info(f'    설명: {self.scenario.description}')

        self._pub = self.create_publisher(Float32, output_topic, 10)

        # 시간축 재생 — start_ns 기준 wall time (G2 player와 일관, /clock 회복 후
        # use_sim_time 전환은 별 backlog).
        self._start_ns = self.get_clock().now().nanoseconds
        self._segment_starts_s: List[float] = []
        acc = 0.0
        for s in self.scenario.segments:
            self._segment_starts_s.append(acc)
            acc += s.duration_s
        self._total_segments_end_s = acc
        self._session_end_s = acc + self.scenario.finish_hover_s

        self._last_logged_segment: int = -1

        period = 1.0 / self.scenario.publish_rate_hz
        self._timer = self.create_timer(period, self._on_timer)

    def _eval_at(self, elapsed_s: float) -> Optional[float]:
        """현재 elapsed에서의 c 값 평가.

        finish_hover 구간은 마지막 segment의 *종료 값*을 hold.
        stall segment 구간은 발행 억제를 뜻하는 None 을 반환한다(ADR-0050 D3).
        """
        if elapsed_s >= self._total_segments_end_s:
            return self._final_value()

        idx = 0
        for i, start_s in enumerate(self._segment_starts_s):
            if elapsed_s >= start_s:
                idx = i
            else:
                break
        seg = self.scenario.segments[idx]
        seg_t = elapsed_s - self._segment_starts_s[idx]

        if seg.type == 'constant':
            value = seg.value
        elif seg.type == 'step':
            value = seg.value  # 즉시 점프 후 끝까지 hold (segment 내부 균등 = constant와 동일,
                               # *시나리오 레벨*에서 직전 segment 대비 점프 의미).
        elif seg.type == 'stall':
            self._log_segment_entry(idx, elapsed_s)
            return None  # 발행 억제 — LLM 무응답 모사.
        else:  # ramp
            frac = seg_t / max(seg.duration_s, 1e-9)
            frac = max(0.0, min(1.0, frac))
            value = seg.val_from + frac * (seg.val_to - seg.val_from)

        self._log_segment_entry(idx, elapsed_s)
        return _clamp01(value)

    def _log_segment_entry(self, idx: int, elapsed_s: float) -> None:
        """새 segment 진입 시 1회 로그."""
        if idx == self._last_logged_segment:
            return
        seg = self.scenario.segments[idx]
        note_str = f' — {seg.note}' if seg.note else ''
        if seg.type == 'ramp':
            desc = f'ramp {seg.val_from:.2f} → {seg.val_to:.2f}'
        elif seg.type == 'stall':
            desc = 'stall (발행 억제)'
        else:
            desc = f'{seg.type} {seg.value:.2f}'
        self.get_logger().info(
            f'[intent_c] segment {idx+1}/{len(self.scenario.segments)} '
            f'(t={elapsed_s:.1f}s, dur={seg.duration_s:.1f}s) {desc}{note_str}'
        )
        self._last_logged_segment = idx

    def _final_value(self) -> float:
        """마지막 segment의 종료 값.

        마지막이 stall 이면 직전 non-stall segment 의 종료 값으로 hold
        (stall 로 시나리오가 끝나는 구성은 권장하지 않음 — resume segment 를 둘 것).
        """
        for seg in reversed(self.scenario.segments):
            if seg.type == 'stall':
                continue
            if seg.type == 'ramp':
                return _clamp01(seg.val_to)
            return _clamp01(seg.value)
        return 1.0

    def _on_timer(self) -> None:
        elapsed_s = (self.get_clock().now().nanoseconds - self._start_ns) * 1e-9

        if elapsed_s >= self._session_end_s:
            self._publish(self._final_value())
            self.get_logger().info(
                f'[intent_c] 시나리오 "{self.scenario.name}" 완료 ({elapsed_s:.1f}s).'
            )
            if self.exit_on_finish:
                rclpy.shutdown()
                return
            self._timer.cancel()
            return

        c = self._eval_at(elapsed_s)
        if c is None:
            return  # stall 구간 — 발행 억제(LLM 무응답 모사, ADR-0050 D3).
        self._publish(c)

    def _publish(self, c: float) -> None:
        msg = Float32()
        msg.data = float(c)
        self._pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = ConfidencePublisherNode()
    except Exception as e:
        print(f'[intent_c] 초기화 실패: {e}', file=__import__('sys').stderr)
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
