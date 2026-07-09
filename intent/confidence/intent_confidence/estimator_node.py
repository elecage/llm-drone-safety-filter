"""estimator_node — 의도해석기 raw 신호 → c̃ 변환기 (A3-3).

ADR-0020 D1 (곱셈형 g) + D4 (rate limiter, cmsm-proof §6) 의 *ROS 2 노드 래퍼*.

두 입력 모드 ([ADR-0020 Amendment 2026-05-31](../../../docs/handover/decisions/0020-confidence-estimator-g-form-lock.md)):

  - **synthesis** (default): YAML 시나리오 (`signals_*.yaml`) 의 raw 신호 시계열을
    `evaluate_signals_at` 으로 합성 재생. paper §C calibration 분포 재생 + 수식
    단위 검증.
  - **live**: 실 OVD/LLM 출력 위 산출. `/intent/ovd/detections` (Detection2DArray)
    → s1, `/intent/llm_sigma_raw` (signals) → s2/s3. paper §C sweep 의 strict
    e2e 길 (RQ1 입증 본실험). 토픽 미수신·신호 부재 → raw c=0 fail-safe (D3).

publisher_node 와의 차이:
  - publisher_node : YAML 의 c 시계열을 *직접* /intent/grounding_confidence publish.
  - estimator_node : raw 신호 (s1, s2, s3) → compute_g → rate_limit_step → 동일 토픽.

진단 채널 (ADR-0020 D3 amendment):
  - /intent/estimator/report (std_msgs/String JSON) — EstimatorReport dataclass.
    paper §C 가 *(a) 부재 vs (b) 낮은 신뢰도* 분리 보고 의무 충족. live 모드는
    `s1_reason` (no_detections|no_referent|no_match|stale) 로 부재 사유 보고.

YAML signal 시나리오 스키마 (synthesis 모드, `scenarios/signals_*.yaml`):
    name: <str>
    description: <str>
    publish_rate_hz: <float>     # 기본 10
    finish_hover_s: <float>      # 기본 2
    dot_c_max: <float>           # 선택 — launch 인자 dot_c_max 가 우선
    segments:
      - duration_s: <float>
        type: constant | ramp | step
        s1: <float>; s2: <float>; s3: <float>       # constant/step
        s1_from / s1_to / ... : <float>             # ramp
        s1_absent / s2_absent / s3_absent: <bool>   # 부재 플래그
        note: <str>

순수 로직 — synthesis 는 [signal_scenario](signal_scenario.py), live 는
[live_signals](live_signals.py) 모듈에 분리 (host venv 에서 rclpy 없이 단위 테스트).
"""

from __future__ import annotations

import sys
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
    qos_profile_sensor_data,
)

from std_msgs.msg import Bool, Float32, String

from intent_confidence.estimator import GInputs, compute_g, rate_limit_step
from intent_confidence.live_signals import (
    DetectionCandidate,
    ParsedSigma,
    S1Result,
    compute_s1,
    parse_sigma_raw,
    resolve_active_sigma,
    resolve_grounded_s1,
    sanitize_detection_score,
)
from intent_confidence.signal_scenario import (
    DOT_C_MAX_DEFAULT,
    EstimatorReport,
    evaluate_signals_at,
    load_signal_scenario,
    resolve_dot_c_max,
    segment_starts_seconds,
)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class EstimatorNode(Node):
    def __init__(self) -> None:
        super().__init__('intent_confidence_estimator')

        # --- 공통 파라미터 ---
        self.declare_parameter('estimator_mode', 'synthesis')  # 'synthesis' | 'live'
        self.declare_parameter('output_topic', '/intent/grounding_confidence')
        self.declare_parameter('report_topic', '/intent/estimator/report')
        self.declare_parameter('scenario_file', '')
        # dot_c_max sentinel = -1.0 → "launch 인자 미지정". PR #69 review §1
        # follow-up: 0.833 default 가 "사용자 명시 0.833" 과 구분 안 됐던 fragile
        # 휴리스틱을 sentinel + 명시적 fallback chain 으로 교체.
        self.declare_parameter('dot_c_max', -1.0)
        self.declare_parameter('initial_c_tilde', 1.0)
        self.declare_parameter('exit_on_finish', False)
        # --- live 모드 전용 파라미터 ---
        self.declare_parameter('ovd_detection_topic', '/intent/ovd/detections')
        self.declare_parameter('sigma_raw_topic', '/intent/llm_sigma_raw')
        self.declare_parameter('signal_timeout_s', 1.0)   # OVD detection stale 윈도
        # referent latch (ADR-0020 amendment 2026-06-11 — 발견 A): LLM sigma 는
        # 발화당 1회 이벤트라 OVD 연속 staleness 와 분리해 *새 sigma 대체까지* latch.
        # 0 이하 = 무한(대체까지 지속). 양수면 그 TTL 후 만료 → 부재 → c=0.
        self.declare_parameter('sigma_latch_timeout_s', 0.0)
        # grounding s1 freeze 안정 윈도우 [s] (ADR-0038 D2): vantage 도달 직후 첫
        # 'ok' 을 즉시 동결하면 카메라/OVD 가 후보를 다 잡기 전(예 S7 의자 2개 중
        # 1개)의 s1 을 freeze 해 거짓 단일화한다. 윈도우 동안은 *더 낮은 s1*(더 모호)
        # 으로만 갱신 → 검출 안정 후 동결. 0 = 즉시 동결(종전).
        # ADR-0040 Phase 2 — 360° 검색 스윕 동안 min-rule 이 *가장 모호한*(후보 최다)
        # 시점을 포착하도록 freeze 윈도우를 스윕 전체로 확장(종전 1.5s 는 스윕 도중
        # 만료돼 초기 부분뷰를 동결). 스윕 ~10–15s 를 덮는 12s.
        self.declare_parameter('s1_freeze_window_s', 12.0)
        # ADR-0040 D8 (세션 61) — min-rule 시간 debounce. 더 낮은 s1 갱신을 이만큼
        # 연속 'ok' 프레임 동안 유지될 때만 latch 반영 → OVD 단일프레임 중복박스
        # 아티팩트(~0.75% flicker) 기각, 지속적 모호성(S5/S7)만 반영. 10Hz 기준 3=0.3s.
        self.declare_parameter('s1_min_persist_frames', 3)
        self.declare_parameter('publish_rate_hz', 10.0)   # live 모드 timer 주기
        # grounding gate (ADR-0031 D3) — sigma_bridge 가 inspect vantage 도달 전
        # 닫는 Bool 토픽. 닫힌 동안 s1 latch 보류(도달 전 빈 s1 동결 차단). 미수신
        # 기본 open(True) — 기존 동작·gate 없는 경로(move_to 등) 보존.
        self.declare_parameter('grounding_gate_topic', '/intent/grounding_gate')

        self.mode = str(self.get_parameter('estimator_mode').value).strip().lower()
        if self.mode not in ('synthesis', 'live'):
            raise RuntimeError(
                f"estimator_mode 는 'synthesis' | 'live': {self.mode!r}"
            )

        output_topic = self.get_parameter('output_topic').value
        report_topic = self.get_parameter('report_topic').value
        dot_c_max_arg = float(self.get_parameter('dot_c_max').value)
        self.exit_on_finish = bool(self.get_parameter('exit_on_finish').value)
        self.c_tilde = _clamp01(float(self.get_parameter('initial_c_tilde').value))

        self._pub_c = self.create_publisher(Float32, output_topic, 10)
        self._pub_report = self.create_publisher(String, report_topic, 10)
        self._start_ns = self.get_clock().now().nanoseconds

        if self.mode == 'synthesis':
            self._init_synthesis(dot_c_max_arg, output_topic, report_topic)
        else:
            self._init_live(dot_c_max_arg, output_topic, report_topic)

    # ====================================================================
    # synthesis 모드
    # ====================================================================

    def _init_synthesis(self, dot_c_max_arg, output_topic, report_topic) -> None:
        scenario_path_str = self.get_parameter('scenario_file').value
        if not scenario_path_str:
            raise RuntimeError(
                'synthesis 모드: scenario_file 파라미터 필수 (YAML 절대경로 또는 '
                'share 상대경로)'
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

        self.scenario = load_signal_scenario(scenario_path)

        # dot_c_max 우선순위 (PR #69 review §1 follow-up — sentinel chain):
        #   1. launch 인자가 양수 → 사용자 명시 → 인자 우선
        #   2. YAML 의 dot_c_max: 키가 양수 → YAML 우선
        #   3. 둘 다 미지정 → cmsm-proof §7.1 시안 default 0.833
        self.dot_c_max = resolve_dot_c_max(dot_c_max_arg, self.scenario.dot_c_max_yaml)

        total_duration = (
            sum(s.duration_s for s in self.scenario.segments)
            + self.scenario.finish_hover_s
        )
        self.get_logger().info(
            f'estimator_node[synthesis] 시작 — scenario="{self.scenario.name}", '
            f'segments={len(self.scenario.segments)}, total≈{total_duration:.1f}s, '
            f'rate={self.scenario.publish_rate_hz:.1f}Hz, dot_c_max={self.dot_c_max:.3f}, '
            f'init c̃={self.c_tilde:.3f}, output={output_topic}, report={report_topic}'
        )
        self.get_logger().info(f'    설명: {self.scenario.description}')

        self._segment_starts_s = segment_starts_seconds(self.scenario)
        if self.scenario.segments:
            self._total_segments_end_s = (
                self._segment_starts_s[-1] + self.scenario.segments[-1].duration_s
            )
        else:
            self._total_segments_end_s = 0.0
        self._session_end_s = self._total_segments_end_s + self.scenario.finish_hover_s
        self._last_logged_segment = -1

        period = 1.0 / self.scenario.publish_rate_hz
        # 첫 tick 의 dt 가 정확히 period 가 되도록 last_ns 를 한 period 뒤로 밀어 둠
        # (PR #69 review §2 follow-up).
        self._last_ns = self._start_ns - int(period * 1e9)
        self._timer = self.create_timer(period, self._on_timer_synthesis)

    def _on_timer_synthesis(self) -> None:
        now_ns = self.get_clock().now().nanoseconds
        elapsed_s = (now_ns - self._start_ns) * 1e-9
        dt = (now_ns - self._last_ns) * 1e-9
        self._last_ns = now_ns

        finished = elapsed_s >= self._session_end_s

        signals, idx = evaluate_signals_at(
            self.scenario,
            self._segment_starts_s,
            min(elapsed_s, self._session_end_s - 1e-6),
        )
        c_raw = compute_g(signals)
        c_tilde_prev = self.c_tilde
        self.c_tilde = rate_limit_step(c_raw, self.c_tilde, max(dt, 1e-9), self.dot_c_max)

        # segment 진입 로그.
        if not finished and idx != self._last_logged_segment:
            seg = self.scenario.segments[idx]
            note_str = f' — {seg.note}' if seg.note else ''
            if seg.type == 'ramp':
                desc = (
                    f'ramp s1 {seg.s1_from:.2f}→{seg.s1_to:.2f} · '
                    f's2 {seg.s2_from:.2f}→{seg.s2_to:.2f} · '
                    f's3 {seg.s3_from:.2f}→{seg.s3_to:.2f}'
                )
            else:
                desc = f'{seg.type} s1={seg.s1:.2f} s2={seg.s2:.2f} s3={seg.s3:.2f}'
            absent = []
            if seg.s1_absent: absent.append('s1')
            if seg.s2_absent: absent.append('s2')
            if seg.s3_absent: absent.append('s3')
            absent_str = f' [absent={absent}]' if absent else ''
            self.get_logger().info(
                f'[estimator] segment {idx+1}/{len(self.scenario.segments)} '
                f'(t={elapsed_s:.1f}s, dur={seg.duration_s:.1f}s) '
                f'{desc}{absent_str}{note_str}'
            )
            self._last_logged_segment = idx

        self._publish(
            signals, idx, elapsed_s, c_raw, c_tilde_prev, dt,
            scenario_name=self.scenario.name,
        )

        if finished:
            self.get_logger().info(
                f'[estimator] 시나리오 "{self.scenario.name}" 완료 ({elapsed_s:.1f}s).'
            )
            if self.exit_on_finish:
                rclpy.shutdown()
                return
            self._timer.cancel()

    # ====================================================================
    # live 모드 (ADR-0020 Amendment 2026-05-31)
    # ====================================================================

    def _init_live(self, dot_c_max_arg, output_topic, report_topic) -> None:
        if self.get_parameter('scenario_file').value:
            self.get_logger().warn(
                'live 모드: scenario_file 무시됨 (synthesis 전용 파라미터).'
            )
        # live 모드는 YAML 의 dot_c_max 가 없으므로 launch 인자 또는 §7.1 default.
        self.dot_c_max = resolve_dot_c_max(dot_c_max_arg, -1.0)
        self.publish_rate_hz = float(self.get_parameter('publish_rate_hz').value)
        if self.publish_rate_hz <= 0.0:
            raise RuntimeError(f'publish_rate_hz 는 양수: {self.publish_rate_hz}')
        self.signal_timeout_s = float(self.get_parameter('signal_timeout_s').value)
        # latch TTL: 0 이하 → 무한(대체까지). ns 로 보관 (resolve_active_sigma 계약).
        latch_s = float(self.get_parameter('sigma_latch_timeout_s').value)
        self.sigma_latch_timeout_ns = int(latch_s * 1e9) if latch_s > 0.0 else 0
        # grounding s1 freeze 안정 윈도우 (ns). 0 이하 = 즉시 동결.
        freeze_s = float(self.get_parameter('s1_freeze_window_s').value)
        self._s1_freeze_window_ns = int(freeze_s * 1e9) if freeze_s > 0.0 else 0
        # ADR-0040 D8 — min-rule debounce 연속 'ok' 프레임 수.
        self._s1_min_persist_frames = int(self.get_parameter('s1_min_persist_frames').value)
        ovd_topic = str(self.get_parameter('ovd_detection_topic').value)
        sigma_topic = str(self.get_parameter('sigma_raw_topic').value)

        # live 입력 상태 — 콜백이 갱신, timer 가 소비. stamp None = 미수신.
        self._latest_detections: list = []
        self._ovd_stamp_ns = None
        self._latest_sigma: ParsedSigma | None = None
        self._sigma_stamp_ns = None
        # grounding 시점 s1 latch (ADR-0029 블로커 2) — (s1, sigma_stamp_ns) 또는 None.
        self._s1_latch: tuple | None = None
        # grounding gate (ADR-0031 D3) — sigma_bridge inspect vantage 도달 신호.
        # 기본 open(True): gate 미수신·gate 없는 경로(move_to)에서 기존 동작 보존.
        self._grounding_open: bool = True
        gate_topic = str(self.get_parameter('grounding_gate_topic').value)

        # OVD detections 구독 (Detection2DArray). import 는 live 모드에서만 —
        # synthesis 트랙·host 단위 테스트가 vision_msgs 에 의존하지 않도록.
        from vision_msgs.msg import Detection2DArray
        self._sub_ovd = self.create_subscription(
            Detection2DArray, ovd_topic, self._on_detections, qos_profile_sensor_data
        )
        self._sub_sigma = self.create_subscription(
            String, sigma_topic, self._on_sigma_raw, 10
        )
        # gate publisher(sigma_bridge)는 transient_local(latching) — 늦게 뜬
        # estimator 도 마지막 상태를 받도록 동일 durability 로 구독.
        self._sub_gate = self.create_subscription(
            Bool, gate_topic, self._on_grounding_gate,
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=1,
            ),
        )

        latch_desc = (
            '무한(대체까지)' if self.sigma_latch_timeout_ns == 0
            else f'{self.sigma_latch_timeout_ns * 1e-9:.1f}s'
        )
        self.get_logger().info(
            f'estimator_node[live] 시작 — rate={self.publish_rate_hz:.1f}Hz, '
            f'dot_c_max={self.dot_c_max:.3f}, init c̃={self.c_tilde:.3f}, '
            f'ovd_timeout={self.signal_timeout_s:.2f}s, sigma_latch={latch_desc}, '
            f'ovd={ovd_topic}, sigma_raw={sigma_topic}, output={output_topic}, '
            f'report={report_topic}'
        )

        period = 1.0 / self.publish_rate_hz
        self._last_ns = self._start_ns - int(period * 1e9)
        self._timer = self.create_timer(period, self._on_timer_live)

    def _on_detections(self, msg) -> None:
        """Detection2DArray → DetectionCandidate 리스트 + 수신 stamp."""
        cands = []
        n_nonfinite = 0
        for det in msg.detections:
            if not det.results:
                continue
            hyp = det.results[0].hypothesis
            # 비유한(NaN/Inf) score 가 compute_g 도메인 검증 예외 → timer 콜백
            # 사망으로 이어지는 경로 차단 — 0.0(보수) 복구 (세션 34 리뷰 후속).
            score, finite = sanitize_detection_score(float(hyp.score))
            if not finite:
                n_nonfinite += 1
            # bbox (cx, cy, w, h) — ADR-0040 D7 동일 라벨 중복박스 dedup용.
            bb = det.bbox
            bbox = (
                float(bb.center.position.x), float(bb.center.position.y),
                float(bb.size_x), float(bb.size_y),
            )
            cands.append(DetectionCandidate(
                class_label=str(hyp.class_id),
                confidence=score,
                bbox=bbox,
            ))
        if n_nonfinite:
            self.get_logger().warn(
                f'OVD detections 비유한값 score {n_nonfinite}건 → 0.0(보수) 복구',
                throttle_duration_sec=2.0,
            )
        self._latest_detections = cands
        self._ovd_stamp_ns = self.get_clock().now().nanoseconds

    def _on_sigma_raw(self, msg) -> None:
        """sigma_raw String → ParsedSigma (s2/s3 + referent) + 수신 stamp."""
        self._latest_sigma = parse_sigma_raw(msg.data)
        if not self._latest_sigma.parse_ok:
            # 파싱 실패는 absent 플래그로 c 보수 방향이라 안전하지만, 무로깅이면
            # 상류 wrapper 고장을 운용 중 진단 불가 (세션 34 리뷰 후속).
            self.get_logger().warn(
                f'sigma_raw JSON 파싱 실패 — s2/s3 absent (c 보수 방향). '
                f'payload 선두: {msg.data[:80]!r}',
                throttle_duration_sec=2.0,
            )
        self._sigma_stamp_ns = self.get_clock().now().nanoseconds

    def _on_grounding_gate(self, msg) -> None:
        """grounding gate (Bool) — sigma_bridge inspect vantage 도달 신호 (ADR-0031 D3).

        닫힘(False) 동안 estimator 가 s1 latch 를 보류(sigma_active=False 취급)해
        vantage 도달 *전* 의 빈 s1(no_detections)이 동결되는 것을 막는다. 열림(True)
        이면 정상 grounding. 미수신 기본은 open(생성자 self._grounding_open=True).
        """
        self._grounding_open = bool(msg.data)

    def _on_timer_live(self) -> None:
        now_ns = self.get_clock().now().nanoseconds
        elapsed_s = (now_ns - self._start_ns) * 1e-9
        dt = (now_ns - self._last_ns) * 1e-9
        self._last_ns = now_ns

        ovd_timeout_ns = int(self.signal_timeout_s * 1e9)

        # --- s2/s3 (sigma_raw) + referent — latch (발견 A) ---
        # LLM 의도는 발화당 1회 이벤트 → OVD 연속 staleness 와 분리해 새 sigma
        # 대체까지(또는 TTL) latch 유지. resolve_active_sigma 가 순수 해소.
        sigma_age_ns = (
            None if self._sigma_stamp_ns is None
            else now_ns - self._sigma_stamp_ns
        )
        active = resolve_active_sigma(
            self._latest_sigma, sigma_age_ns, self.sigma_latch_timeout_ns
        )
        s2, s3 = active.s2, active.s3
        s2_absent, s3_absent = active.s2_absent, active.s3_absent
        referent = active.referent_labels

        # --- s1 (OVD detections + referent) — 연속, 짧은 stale 윈도 ---
        ovd_stale = (
            self._ovd_stamp_ns is None
            or (now_ns - self._ovd_stamp_ns) > ovd_timeout_ns
        )
        if ovd_stale:
            live_s1 = S1Result(0.0, True, 'stale', 0, 0)
        else:
            live_s1 = compute_s1(self._latest_detections, referent)
        # grounding 시점 s1 latch (ADR-0029 블로커 2) — 같은 명령(σ) 안에서 한 번
        # grounding 되면 이후 OVD 끊김(inspect 이동 시 대상 FOV 이탈)에도 s1 유지.
        # grounding gate (ADR-0031 D3) — vantage 도달 전(gate 닫힘)엔 grounding 보류
        # (빈 s1 동결 차단). gate 미수신 기본 open 이라 gate 없는 경로는 종전대로.
        sigma_active = active.latched and bool(referent) and self._grounding_open
        # ADR-0040 Phase 2 — latch 키 = referent 동일성(σ stamp 아님). σ 가 ~6s 주기로
        # 재발행돼도(같은 발화) referent 동일하면 latch 유지 → 360° 스윕 중 grounding
        # 지속(세션 60 진단: stamp 키는 매 재발행 리셋 → c 붕괴).
        command_key = tuple(referent) if referent else None
        grounded, self._s1_latch = resolve_grounded_s1(
            live_s1, sigma_active, command_key, self._s1_latch,
            now_ns, self._s1_freeze_window_ns, self._s1_min_persist_frames,
        )
        s1, s1_absent, s1_reason, n_det = (
            grounded.s1, grounded.absent, grounded.reason, grounded.n_detections
        )

        signals = GInputs(
            s1=s1, s2=s2, s3=s3,
            s1_absent=s1_absent, s2_absent=s2_absent, s3_absent=s3_absent,
            s3_structural=active.s3_structural,  # ADR-0020 D8 — edge 곱 제외
        )
        c_raw = compute_g(signals)
        c_tilde_prev = self.c_tilde
        self.c_tilde = rate_limit_step(c_raw, self.c_tilde, max(dt, 1e-9), self.dot_c_max)

        self._publish(
            signals, -1, elapsed_s, c_raw, c_tilde_prev, dt,
            scenario_name='live',
            s1_reason=s1_reason,
            referent_labels=','.join(referent),
            n_detections=n_det,
            sigma_age_s=active.age_s,
            sigma_latched=active.latched,
        )

    # ====================================================================
    # 공통 publish
    # ====================================================================

    def _publish(
        self,
        signals,
        idx: int,
        elapsed_s: float,
        c_raw: float,
        c_tilde_prev: float,
        dt: float,
        *,
        scenario_name: str,
        s1_reason: str = 'ok',
        referent_labels: str = '',
        n_detections: int = -1,
        sigma_age_s: float = -1.0,
        sigma_latched: bool = False,
    ) -> None:
        msg_c = Float32()
        msg_c.data = float(self.c_tilde)
        self._pub_c.publish(msg_c)

        delta_req = c_raw - c_tilde_prev
        delta_app = self.c_tilde - c_tilde_prev
        clamped = abs(delta_req) > self.dot_c_max * max(dt, 1e-9) + 1e-9

        report = EstimatorReport(
            stamp_ns=self.get_clock().now().nanoseconds,
            elapsed_s=elapsed_s,
            scenario_name=scenario_name,
            segment_idx=idx,
            s1=signals.s1, s2=signals.s2, s3=signals.s3,
            s1_absent=signals.s1_absent,
            s2_absent=signals.s2_absent,
            s3_absent=signals.s3_absent,
            c_raw=c_raw,
            c_tilde=self.c_tilde,
            c_tilde_prev=c_tilde_prev,
            dot_c_max=self.dot_c_max,
            delta_c_clamped=clamped,
            delta_c_requested=delta_req,
            delta_c_applied=delta_app,
            s1_reason=s1_reason,
            referent_labels=referent_labels,
            n_detections=n_detections,
            sigma_age_s=sigma_age_s,
            sigma_latched=sigma_latched,
            s3_structural=getattr(signals, 's3_structural', False),
        )
        msg_report = String()
        msg_report.data = report.to_json()
        self._pub_report.publish(msg_report)


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = EstimatorNode()
    except Exception as e:
        print(f'[estimator] 초기화 실패: {e}', file=sys.stderr)
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
