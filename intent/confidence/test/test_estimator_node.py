"""estimator_node 단위 테스트 — rclpy 의존성 *없이* YAML loader + 평가 함수만.

노드 자체 (rclpy timer · subscriber 등) 는 `colcon test` Docker 트랙에서 검증.
본 테스트는 host venv 에서 yaml 만 있으면 통과 — pure logic 검증.

PR #69 review 후속 (2026-05-26): 1+2+3+4 묶음 테스트 추가.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from intent_confidence.estimator import GInputs, compute_g, rate_limit_step
from intent_confidence.signal_scenario import (
    DOT_C_MAX_DEFAULT,
    EstimatorReport,
    SignalScenario,
    SignalSegment,
    evaluate_signals_at,
    load_signal_scenario,
    resolve_dot_c_max,
    segment_starts_seconds,
)


SCENARIO_DIR = Path(__file__).parent.parent / 'scenarios'


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

class TestLoadSignalScenario:
    def test_loads_high_confidence_yaml(self):
        path = SCENARIO_DIR / 'signals_high_confidence.yaml'
        scenario = load_signal_scenario(path)
        assert scenario.name == 'signals_high_confidence'
        assert scenario.publish_rate_hz == 10.0
        assert len(scenario.segments) == 1
        seg = scenario.segments[0]
        assert seg.type == 'constant'
        assert seg.s1 == 0.95
        assert seg.s2 == 0.95
        assert seg.s3 == 0.95
        assert seg.s1_absent is False

    def test_loads_s1_drop_ramp(self):
        path = SCENARIO_DIR / 'signals_s1_drop.yaml'
        scenario = load_signal_scenario(path)
        assert len(scenario.segments) == 3
        ramp = scenario.segments[1]
        assert ramp.type == 'ramp'
        assert ramp.s1_from == 0.95
        assert ramp.s1_to == 0.2
        # s2/s3 는 stable.
        assert ramp.s2_from == 0.9
        assert ramp.s2_to == 0.9

    def test_loads_absent_flag(self):
        path = SCENARIO_DIR / 'signals_s1_absent.yaml'
        scenario = load_signal_scenario(path)
        assert len(scenario.segments) == 2
        absent_seg = scenario.segments[1]
        assert absent_seg.s1_absent is True
        assert absent_seg.s2_absent is False
        assert absent_seg.s3_absent is False

    def test_loads_step_segment(self):
        path = SCENARIO_DIR / 'signals_step_down.yaml'
        scenario = load_signal_scenario(path)
        assert len(scenario.segments) == 2
        step = scenario.segments[1]
        assert step.type == 'step'
        assert step.s1 == 0.2

    def test_loads_low_confidence(self):
        path = SCENARIO_DIR / 'signals_low_confidence.yaml'
        scenario = load_signal_scenario(path)
        assert scenario.segments[0].s1 == 0.5
        # ADR-0020 under-estimation: 0.5^3 = 0.125.
        signals = GInputs(s1=0.5, s2=0.5, s3=0.5)
        assert compute_g(signals) == pytest.approx(0.125)

    def test_rejects_invalid_segment_type(self, tmp_path):
        bad = tmp_path / 'bad.yaml'
        bad.write_text(
            'name: bad\n'
            'description: x\n'
            'segments:\n'
            '  - duration_s: 1.0\n'
            '    type: bogus\n'
            '    s1: 1.0\n'
            '    s2: 1.0\n'
            '    s3: 1.0\n'
        )
        with pytest.raises(ValueError, match='segment.type'):
            load_signal_scenario(bad)

    def test_clamp01_on_load(self, tmp_path):
        # 1.5 같은 out-of-range 값이 _clamp01 으로 안전 처리.
        path = tmp_path / 'oor.yaml'
        path.write_text(
            'name: oor\n'
            'description: x\n'
            'segments:\n'
            '  - duration_s: 1.0\n'
            '    type: constant\n'
            '    s1: 1.5\n'
            '    s2: -0.3\n'
            '    s3: 0.7\n'
        )
        scenario = load_signal_scenario(path)
        seg = scenario.segments[0]
        assert seg.s1 == 1.0
        assert seg.s2 == 0.0
        assert seg.s3 == 0.7


# ---------------------------------------------------------------------------
# evaluate_signals_at — segment 인덱스 + interpolation
# ---------------------------------------------------------------------------

def _scenario_from_segments(segments, finish_hover_s: float = 0.0) -> SignalScenario:
    return SignalScenario(
        name='test', description='', publish_rate_hz=10.0,
        finish_hover_s=finish_hover_s, dot_c_max_yaml=-1.0,
        segments=segments,
    )


def _segment_starts(segments) -> list[float]:
    return segment_starts_seconds(SignalScenario(
        name='_', description='', publish_rate_hz=10.0,
        finish_hover_s=0.0, dot_c_max_yaml=-1.0,
        segments=segments,
    ))


class TestEvaluateSignalsAt:
    def test_constant_segment_returns_fixed_signals(self):
        segs = [SignalSegment(duration_s=5.0, type='constant', s1=0.9, s2=0.8, s3=0.7)]
        scenario = _scenario_from_segments(segs)
        s, idx = evaluate_signals_at(scenario, _segment_starts(segs), 2.5)
        assert idx == 0
        assert s.s1 == 0.9
        assert s.s2 == 0.8
        assert s.s3 == 0.7

    def test_ramp_segment_linear_interpolates(self):
        segs = [SignalSegment(
            duration_s=10.0, type='ramp',
            s1_from=1.0, s1_to=0.0,
            s2_from=0.9, s2_to=0.9,
            s3_from=0.9, s3_to=0.9,
        )]
        scenario = _scenario_from_segments(segs)
        s, idx = evaluate_signals_at(scenario, _segment_starts(segs), 5.0)
        assert s.s1 == pytest.approx(0.5)
        assert s.s2 == pytest.approx(0.9)

    def test_segment_boundary_advances(self):
        segs = [
            SignalSegment(duration_s=5.0, type='constant', s1=1.0, s2=1.0, s3=1.0),
            SignalSegment(duration_s=5.0, type='constant', s1=0.5, s2=0.5, s3=0.5),
        ]
        scenario = _scenario_from_segments(segs)
        starts = _segment_starts(segs)
        # 5.001 → 두 번째 segment.
        s, idx = evaluate_signals_at(scenario, starts, 5.001)
        assert idx == 1
        assert s.s1 == 0.5

    def test_finish_hover_holds_last_value(self):
        segs = [SignalSegment(duration_s=3.0, type='constant', s1=0.7, s2=0.7, s3=0.7)]
        scenario = _scenario_from_segments(segs, finish_hover_s=5.0)
        s, idx = evaluate_signals_at(scenario, _segment_starts(segs), 6.0)  # 종료 후
        assert idx == 0
        assert s.s1 == 0.7

    def test_ramp_finish_hover_holds_to_value(self):
        segs = [SignalSegment(
            duration_s=10.0, type='ramp',
            s1_from=1.0, s1_to=0.2,
            s2_from=0.9, s2_to=0.5,
            s3_from=0.8, s3_to=0.4,
        )]
        scenario = _scenario_from_segments(segs, finish_hover_s=5.0)
        s, idx = evaluate_signals_at(scenario, _segment_starts(segs), 12.0)
        assert s.s1 == pytest.approx(0.2)
        assert s.s2 == pytest.approx(0.5)
        assert s.s3 == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Pipeline — YAML → compute_g → rate_limit_step (노드 행동 모사)
# ---------------------------------------------------------------------------

class TestPipelineFromYAML:
    def test_high_confidence_pipeline(self):
        scenario = load_signal_scenario(SCENARIO_DIR / 'signals_high_confidence.yaml')
        starts = _segment_starts(scenario.segments)
        s, _ = evaluate_signals_at(scenario, starts, 5.0)
        c_raw = compute_g(s)
        # 0.95^3 ≈ 0.857375
        assert c_raw == pytest.approx(0.857375, abs=1e-5)

    def test_low_confidence_under_estimation(self):
        # ADR-0020 Consequences amendment 입증.
        scenario = load_signal_scenario(SCENARIO_DIR / 'signals_low_confidence.yaml')
        starts = _segment_starts(scenario.segments)
        s, _ = evaluate_signals_at(scenario, starts, 5.0)
        c_raw = compute_g(s)
        assert c_raw == pytest.approx(0.125, abs=1e-9)

    def test_s1_absent_yields_zero_c_raw(self):
        # ADR-0020 D3 fail-safe — absent 플래그가 입력값 무시.
        scenario = load_signal_scenario(SCENARIO_DIR / 'signals_s1_absent.yaml')
        starts = _segment_starts(scenario.segments)
        # 두 번째 segment (t > 5s) 가 s1_absent=true.
        s, idx = evaluate_signals_at(scenario, starts, 6.0)
        assert idx == 1
        assert s.s1_absent is True
        c_raw = compute_g(s)
        assert c_raw == 0.0  # absent 시 sentinel → 0

    def test_step_down_clamps_via_rate_limiter(self):
        # ADR-0020 D4 / cmsm-proof §6 — step 시 clamp 발동.
        scenario = load_signal_scenario(SCENARIO_DIR / 'signals_step_down.yaml')
        starts = _segment_starts(scenario.segments)
        # 첫 segment 끝 (c_raw ≈ 0.857) → 두 번째 segment 시작 (c_raw ≈ 0.008).
        s_before, _ = evaluate_signals_at(scenario, starts, 4.99)
        s_after, _ = evaluate_signals_at(scenario, starts, 5.01)
        c_before = compute_g(s_before)
        c_after = compute_g(s_after)
        assert c_before > 0.85
        assert c_after < 0.01

        # rate_limit_step 한 step (dt=0.1, dot_c_max=0.833) → |Δc̃| ≤ 0.0833.
        c_tilde_prev = c_before
        c_tilde_next = rate_limit_step(c_after, c_tilde_prev, dt=0.1, c_dot_max=0.833)
        assert c_tilde_prev - c_tilde_next == pytest.approx(0.0833, abs=1e-3)

    def test_s1_drop_ramp_pipeline_progression(self):
        scenario = load_signal_scenario(SCENARIO_DIR / 'signals_s1_drop.yaml')
        starts = _segment_starts(scenario.segments)
        # t=5 (ramp 시작) vs t=10 (ramp 끝).
        s_start, idx0 = evaluate_signals_at(scenario, starts, 5.001)
        s_end, idx1 = evaluate_signals_at(scenario, starts, 9.999)
        assert idx0 == 1 and idx1 == 1
        c_start = compute_g(s_start)
        c_end = compute_g(s_end)
        # 0.95·0.9·0.9 = 0.7695 → 0.2·0.9·0.9 = 0.162
        assert c_start == pytest.approx(0.7695, abs=1e-3)
        assert c_end == pytest.approx(0.162, abs=1e-3)


# ---------------------------------------------------------------------------
# EstimatorReport JSON 직렬화
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PR #69 review §1 follow-up — dot_c_max 우선순위 sentinel chain
# ---------------------------------------------------------------------------

class TestResolveDotCMax:
    """resolve_dot_c_max — sentinel 우선순위 검증.

    sentinel = 음수 또는 0 (launch default = -1.0). chain: arg > 0 → YAML > 0 → 0.833.
    """

    def test_launch_arg_positive_takes_priority(self):
        # arg=0.5 > 0 → arg 우선 (YAML 무관).
        assert resolve_dot_c_max(arg=0.5, yaml_value=2.0) == 0.5

    def test_yaml_used_when_arg_is_sentinel(self):
        # arg=-1.0 (sentinel) + yaml=0.6 → yaml.
        assert resolve_dot_c_max(arg=-1.0, yaml_value=0.6) == 0.6

    def test_default_when_both_sentinel(self):
        # arg=-1.0, yaml=-1.0 → 0.833 default.
        assert resolve_dot_c_max(arg=-1.0, yaml_value=-1.0) == DOT_C_MAX_DEFAULT
        assert DOT_C_MAX_DEFAULT == pytest.approx(0.833)

    def test_explicit_0833_distinguished_from_default(self):
        """PR #69 review §1 의 fragility — 사용자가 명시 0.833 입력해도
        sentinel chain 이 정확히 arg 분기 우선."""
        # 사용자가 진짜 0.833 명시 + YAML 에 2.0 명시 → arg=0.833 우선.
        assert resolve_dot_c_max(arg=0.833, yaml_value=2.0) == 0.833

    def test_zero_arg_treated_as_sentinel(self):
        # 0 은 양수 아님 → sentinel 취급.
        assert resolve_dot_c_max(arg=0.0, yaml_value=0.7) == 0.7


# ---------------------------------------------------------------------------
# PR #69 review §3 follow-up — s_i + s_i_absent 충돌 warning
# ---------------------------------------------------------------------------

class TestAbsentConflictWarning:
    def test_warning_when_absent_and_nonzero_value(self, tmp_path):
        path = tmp_path / 'conflict.yaml'
        path.write_text(
            'name: conflict\n'
            'description: x\n'
            'segments:\n'
            '  - duration_s: 1.0\n'
            '    type: constant\n'
            '    s1: 0.9\n'
            '    s2: 0.0\n'
            '    s3: 0.0\n'
            '    s1_absent: true\n'
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            load_signal_scenario(path)
        assert any('s1_absent=True' in str(w.message) for w in caught)

    def test_no_warning_when_absent_and_zero_value(self, tmp_path):
        path = tmp_path / 'clean.yaml'
        path.write_text(
            'name: clean\n'
            'description: x\n'
            'segments:\n'
            '  - duration_s: 1.0\n'
            '    type: constant\n'
            '    s1: 0.0\n'
            '    s2: 0.0\n'
            '    s3: 0.0\n'
            '    s1_absent: true\n'
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            load_signal_scenario(path)
        # absent + value=0 = 깔끔한 의도 표현 → warning 없음.
        assert not any('s1_absent=True' in str(w.message) for w in caught)

    def test_no_warning_when_absent_false(self, tmp_path):
        path = tmp_path / 'normal.yaml'
        path.write_text(
            'name: normal\n'
            'description: x\n'
            'segments:\n'
            '  - duration_s: 1.0\n'
            '    type: constant\n'
            '    s1: 0.9\n'
            '    s2: 0.9\n'
            '    s3: 0.9\n'
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            load_signal_scenario(path)
        assert not any('absent' in str(w.message).lower() for w in caught)


# ---------------------------------------------------------------------------
# PR #69 review §4 follow-up — 빈 segments YAML 거부
# ---------------------------------------------------------------------------

class TestEmptySegmentsRejected:
    def test_empty_segments_list_raises(self, tmp_path):
        path = tmp_path / 'empty.yaml'
        path.write_text(
            'name: empty\n'
            'description: x\n'
            'segments: []\n'
        )
        with pytest.raises(ValueError, match='segments 비어 있음'):
            load_signal_scenario(path)

    def test_missing_segments_key_raises(self, tmp_path):
        path = tmp_path / 'nokey.yaml'
        path.write_text(
            'name: nokey\n'
            'description: x\n'
        )
        with pytest.raises(ValueError, match='segments 비어 있음'):
            load_signal_scenario(path)


# ---------------------------------------------------------------------------
# 기존 시나리오 5종 regression — review 후 loader 가 여전히 받아들임
# ---------------------------------------------------------------------------

class TestExistingScenariosStillLoad:
    """5 시나리오 YAML 모두 본 PR 후에도 정상 로드 (warning 없이) 확인."""

    @pytest.mark.parametrize('name', [
        'signals_high_confidence',
        'signals_low_confidence',
        'signals_s1_drop',
        'signals_s1_absent',
        'signals_step_down',
    ])
    def test_no_warning_no_error(self, name):
        path = SCENARIO_DIR / f'{name}.yaml'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            scenario = load_signal_scenario(path)
        # s1_absent.yaml 의 s1 값이 0 으로 정정됐으니 warning 없어야.
        absent_warnings = [w for w in caught if 'absent' in str(w.message).lower()]
        assert not absent_warnings, f'{name}: unexpected warnings {absent_warnings}'
        assert len(scenario.segments) > 0


class TestEstimatorReport:
    def test_to_json_roundtrip(self):
        report = EstimatorReport(
            stamp_ns=123_456_789,
            elapsed_s=5.0,
            scenario_name='test',
            segment_idx=1,
            s1=0.9, s2=0.8, s3=0.7,
            s1_absent=False, s2_absent=False, s3_absent=True,
            c_raw=0.504,
            c_tilde=0.5,
            c_tilde_prev=0.6,
            dot_c_max=0.833,
            delta_c_clamped=True,
            delta_c_requested=-0.096,
            delta_c_applied=-0.0833,
        )
        s = report.to_json()
        d = json.loads(s)
        assert d['scenario_name'] == 'test'
        assert d['s3_absent'] is True
        assert d['delta_c_clamped'] is True
        assert d['c_raw'] == pytest.approx(0.504)
