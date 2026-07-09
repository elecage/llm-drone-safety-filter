"""live_signals 단위 테스트 — rclpy 의존성 *없이* pure 로직만.

ADR-0020 Amendment (2026-05-31) live 모드 신호 source 검증. 노드 자체
(rclpy timer · subscriber · stale 판정) 는 `colcon test` Docker 트랙 + Mac mini
e2e 에서 검증. 본 테스트는 host venv 에서 통과.
"""

from __future__ import annotations

import json
import math

import pytest

from intent_confidence.live_signals import (
    ActiveSigma,
    DetectionCandidate,
    GroundedS1,
    ParsedSigma,
    S1Result,
    SIGNAL_LOGPROB,
    SIGNAL_S3_CAPABILITY,
    SIGNAL_SELF_CONSISTENCY,
    compute_s1,
    parse_sigma_raw,
    resolve_active_sigma,
    resolve_grounded_s1,
    sanitize_detection_score,
)


def _cand(label, conf, pos=None):
    return DetectionCandidate(class_label=label, confidence=conf, position=pos)


# ---------------------------------------------------------------------------
# compute_s1 — OVD 후보 + referent → s1 = 1 - H
# ---------------------------------------------------------------------------

class TestComputeS1:
    def test_no_detections_absent(self):
        r = compute_s1([], ['sofa'])
        assert r.absent is True
        assert r.reason == 'no_detections'
        assert r.s1 == 0.0
        assert r.n_detections == 0

    def test_no_referent_absent(self):
        # detection 은 있지만 referent (target_id) 없음 — direction 명령 등.
        r = compute_s1([_cand('sofa', 0.9)], [])
        assert r.absent is True
        assert r.reason == 'no_referent'
        assert r.s1 == 0.0
        assert r.n_detections == 1

    def test_referent_whitespace_only_treated_as_absent(self):
        r = compute_s1([_cand('sofa', 0.9)], ['', '   '])
        assert r.absent is True
        assert r.reason == 'no_referent'

    def test_no_match_absent(self):
        # referent 가 detection class 중 어디에도 매칭 안 됨.
        r = compute_s1([_cand('chair', 0.9), _cand('table', 0.8)], ['sofa'])
        assert r.absent is True
        assert r.reason == 'no_match'
        assert r.n_matched == 0
        assert r.n_detections == 2

    def test_single_dominant_match_high_s1(self):
        # 단일 매칭 후보 → H=0 → s1=1 (모호성 없음).
        r = compute_s1([_cand('sofa', 0.92), _cand('chair', 0.5)], ['sofa'])
        assert r.absent is False
        assert r.reason == 'ok'
        assert r.n_matched == 1
        assert r.s1 == pytest.approx(1.0)

    def test_uniform_multi_match_low_s1(self):
        # 외형 동일 후보 3개 균일 분포 (S5 mug 류) → H 최대 → s1=0.
        cands = [_cand('mug', 0.7), _cand('mug', 0.7), _cand('mug', 0.7)]
        r = compute_s1(cands, ['mug'])
        assert r.absent is False
        assert r.n_matched == 3
        assert r.s1 == pytest.approx(0.0, abs=1e-9)

    def test_skewed_multi_match_mid_s1(self):
        # 한 후보가 우세하지만 다른 후보도 존재 → 0 < s1 < 1.
        cands = [_cand('book', 0.9), _cand('book', 0.1)]
        r = compute_s1(cands, ['book'])
        assert r.absent is False
        assert 0.0 < r.s1 < 1.0

    def test_multiple_referent_labels(self):
        # referent 가 여러 후보 label 을 허용 (예: 한/영 동의어).
        r = compute_s1([_cand('couch', 0.95)], ['sofa', 'couch'])
        assert r.absent is False
        assert r.n_matched == 1
        assert r.s1 == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# parse_sigma_raw — wrapper sigma_raw JSON → s2/s3 + referent
# ---------------------------------------------------------------------------

class TestParseSigmaRaw:
    # s3 인자 = *logprob* (raw $\overline{\log p_t}$, 보통 음수). parse 는 $\exp$ 후
    # $[0,1]$ clamp 해 s3 신호로 변환 (ADR-0020 amendment 2026-06-11).
    def _payload(self, s2=0.8, s3=-0.36, target_id='sofa', extra_signals=None):
        signals = {}
        if s2 is not None:
            signals[SIGNAL_SELF_CONSISTENCY] = s2
        if s3 is not None:
            signals[SIGNAL_LOGPROB] = s3
        if extra_signals:
            signals.update(extra_signals)
        theta = {}
        if target_id is not None:
            theta['target_id'] = target_id
        return json.dumps({'sigma': 'move_to', 'theta': theta, 'c': 0.5,
                           'signals': signals})

    def test_normal_payload(self):
        r = parse_sigma_raw(self._payload())
        assert r.parse_ok is True
        assert r.s2 == pytest.approx(0.8)
        # s3_logprob=-0.36 → exp(-0.36) ≈ 0.6977.
        assert r.s3 == pytest.approx(math.exp(-0.36))
        assert r.s2_absent is False
        assert r.s3_absent is False
        assert r.referent_labels == ('sofa',)

    def test_s3_logprob_normalized_exp(self):
        # capability 키 부재(default True) + 음수 logprob → exp 정규화.
        r = parse_sigma_raw(self._payload(s3=-2.0))
        assert r.s3_absent is False
        assert r.s3_structural is False
        assert r.s3 == pytest.approx(math.exp(-2.0))

    def test_s3_capability_false_structural(self):
        # ADR-0020 D8 — s3_capability=False (edge) → 구조적 부재, logprob 무시.
        r = parse_sigma_raw(self._payload(
            s3=-99.0, extra_signals={SIGNAL_S3_CAPABILITY: False},
        ))
        assert r.s3_structural is True
        assert r.s3_absent is False     # 런타임 부재 아님 (능력 한계)
        assert r.s3 == pytest.approx(1.0)  # neutral placeholder

    def test_s3_capability_true_runtime_absent(self):
        # capability=True + logprob 부재(None) → 런타임 부재 (c=0).
        r = parse_sigma_raw(self._payload(
            s3=None, extra_signals={SIGNAL_S3_CAPABILITY: True},
        ))
        assert r.s3_structural is False
        assert r.s3_absent is True
        assert r.s3 == pytest.approx(0.0)

    def test_s3_capability_missing_defaults_true(self):
        # 구버전 payload (capability 키 부재) → True 가정 → 런타임 경로.
        r = parse_sigma_raw(self._payload(s3=-0.36))
        assert r.s3_structural is False
        assert r.s3 == pytest.approx(math.exp(-0.36))

    def test_s3_logprob_zero_is_one(self):
        # logprob=0 (확률 1) → exp(0)=1.0.
        r = parse_sigma_raw(self._payload(s3=0.0))
        assert r.s3 == pytest.approx(1.0)

    def test_s3_positive_logprob_clamped(self):
        # 양의 logprob(이론상 비정상) → exp>1 → clamp 1.0 (부재 아님).
        r = parse_sigma_raw(self._payload(s3=0.5))
        assert r.s3_absent is False
        assert r.s3 == pytest.approx(1.0)

    def test_s3_huge_positive_logprob_clamped(self):
        # exp(710) 이상은 OverflowError → clamp 1.0 (노드 죽지 않음).
        r = parse_sigma_raw(self._payload(s3=1000.0))
        assert r.s3_absent is False
        assert r.s3 == pytest.approx(1.0)

    def test_s3_inf_logprob_absent(self):
        bad = ('{"theta": {}, "signals": '
               '{"s2_self_consistency": 0.5, "s3_logprob": Infinity}}')
        r = parse_sigma_raw(bad)
        assert r.s3_absent is True
        assert r.s2_absent is False

    def test_missing_signals_keys_absent(self):
        r = parse_sigma_raw(self._payload(s2=None, s3=None))
        assert r.parse_ok is True
        assert r.s2_absent is True
        assert r.s3_absent is True
        assert r.s2 == 0.0 and r.s3 == 0.0

    def test_direction_command_no_referent(self):
        # move_to.direction (target_id 없음) → 빈 referent → s1 부재로 이어짐.
        payload = json.dumps({
            'sigma': 'move_to',
            'theta': {'direction': 'forward'},
            'c': 0.5,
            'signals': {SIGNAL_SELF_CONSISTENCY: 0.9, SIGNAL_LOGPROB: -0.2},
        })
        r = parse_sigma_raw(payload)
        assert r.referent_labels == ()
        assert r.s2_absent is False

    def test_invalid_json(self):
        r = parse_sigma_raw('{not valid json')
        assert r.parse_ok is False
        assert r.s2_absent is True and r.s3_absent is True
        assert r.referent_labels == ()

    def test_non_dict_payload(self):
        r = parse_sigma_raw('[1, 2, 3]')
        assert r.parse_ok is False

    def test_s2_out_of_range_absent(self):
        # s2 ($\rho$) 는 [0,1] 정의 → 범위 밖 부재. (s3 는 logprob 이라 범위 무관.)
        r = parse_sigma_raw(self._payload(s2=1.5))
        assert r.s2_absent is True

    def test_bool_signal_rejected(self):
        # bool 은 의도 모호 — 부재 취급.
        r = parse_sigma_raw(self._payload(s2=True))
        assert r.s2_absent is True

    def test_nan_signal_absent(self):
        # python json.loads 는 NaN 을 허용(기본) → _coerce 가 걸러냄.
        bad = '{"theta": {}, "signals": {"s2_self_consistency": NaN, "s3_logprob": -0.5}}'
        r = parse_sigma_raw(bad)
        assert r.s2_absent is True
        assert r.s3_absent is False  # -0.5 → exp ≈ 0.607 유효

    def test_empty_target_id_no_referent(self):
        r = parse_sigma_raw(self._payload(target_id='   '))
        assert r.referent_labels == ()

    # ADR-0029 블로커 1 — referent 매칭은 *클래스* 기준. theta.target_class 가 있으면
    # 인스턴스 id(target_id) 대신 그것을 referent 로 사용 (검출기 클래스와 입도 일치).
    def _payload_with_class(self, target_id, target_class):
        theta = {}
        if target_id is not None:
            theta['target_id'] = target_id
        if target_class is not None:
            theta['target_class'] = target_class
        return json.dumps({'sigma': 'inspect', 'theta': theta, 'c': 0.9,
                           'signals': {SIGNAL_SELF_CONSISTENCY: 0.9,
                                       SIGNAL_LOGPROB: -0.1}})

    def test_target_class_takes_precedence_over_target_id(self):
        # 인스턴스 chair_left + 클래스 chair → referent = ('chair',) (OVD 클래스 매칭용).
        r = parse_sigma_raw(self._payload_with_class('chair_left', 'chair'))
        assert r.referent_labels == ('chair',)

    def test_target_id_fallback_when_no_class(self):
        # target_class 부재 → target_id 폴백 (backward compat).
        r = parse_sigma_raw(self._payload_with_class('sofa', None))
        assert r.referent_labels == ('sofa',)

    def test_empty_target_class_falls_back_to_id(self):
        # 공백 target_class → 폴백.
        r = parse_sigma_raw(self._payload_with_class('chair_left', '   '))
        assert r.referent_labels == ('chair_left',)

    def test_target_class_matches_ovd_detections_end_to_end(self):
        # 인스턴스 chair_left referent 가 OVD 'chair' 검출과 매칭 → s1>0 (블로커 1 핵심).
        r = parse_sigma_raw(self._payload_with_class('chair_left', 'chair'))
        s1r = compute_s1([_cand('chair', 0.8)], list(r.referent_labels))
        assert s1r.absent is False
        assert s1r.s1 > 0.0

    def test_missing_theta(self):
        payload = json.dumps({'signals': {SIGNAL_SELF_CONSISTENCY: 0.5,
                                          SIGNAL_LOGPROB: 0.5}})
        r = parse_sigma_raw(payload)
        assert r.referent_labels == ()
        assert r.s2_absent is False


# ---------------------------------------------------------------------------
# 통합 — parse_sigma_raw referent → compute_s1 (live timer 합성 경로 모사)
# ---------------------------------------------------------------------------

class TestLiveCompositionPure:
    def test_grounded_command_yields_high_s1(self):
        sigma = parse_sigma_raw(json.dumps({
            'theta': {'target_id': 'sofa'},
            'signals': {SIGNAL_SELF_CONSISTENCY: 0.9, SIGNAL_LOGPROB: 0.85},
        }))
        s1 = compute_s1([_cand('sofa', 0.9), _cand('chair', 0.4)],
                        sigma.referent_labels)
        assert s1.absent is False
        assert s1.s1 == pytest.approx(1.0)

    def test_direction_command_forces_s1_absent(self):
        sigma = parse_sigma_raw(json.dumps({
            'theta': {'direction': 'left'},
            'signals': {SIGNAL_SELF_CONSISTENCY: 0.9, SIGNAL_LOGPROB: -0.2},
        }))
        s1 = compute_s1([_cand('sofa', 0.9)], sigma.referent_labels)
        assert s1.absent is True
        assert s1.reason == 'no_referent'


# ---------------------------------------------------------------------------
# resolve_active_sigma — referent latch (ADR-0020 amendment 2026-06-11, 발견 A)
# ---------------------------------------------------------------------------

class TestResolveActiveSigma:
    def _sigma(self, s2=0.9, s3=0.85, ref=('sofa',)):
        return ParsedSigma(s2=s2, s3=s3, s2_absent=False, s3_absent=False,
                           referent_labels=ref, parse_ok=True)

    _SEC = 1_000_000_000  # 1s in ns

    def test_none_sigma_absent(self):
        # 미수신 → 부재 + referent 빈 tuple (fail-safe c=0).
        a = resolve_active_sigma(None, None, 0)
        assert a.latched is False
        assert a.s2_absent is True and a.s3_absent is True
        assert a.referent_labels == ()
        assert a.age_s == -1.0

    def test_infinite_latch_holds_old_sigma(self):
        # latch_timeout=0(무한) → 매우 오래된 sigma 도 활성 유지.
        a = resolve_active_sigma(self._sigma(), 60 * self._SEC, 0)
        assert a.latched is True
        assert a.referent_labels == ('sofa',)
        assert a.s2 == pytest.approx(0.9)
        assert a.s3 == pytest.approx(0.85)
        assert a.age_s == pytest.approx(60.0)

    def test_finite_ttl_expires(self):
        # TTL 30s, age 31s → 만료 → 부재.
        a = resolve_active_sigma(self._sigma(), 31 * self._SEC, 30 * self._SEC)
        assert a.latched is False
        assert a.s2_absent is True and a.s3_absent is True
        assert a.referent_labels == ()
        assert a.age_s == pytest.approx(31.0)

    def test_finite_ttl_within_window_active(self):
        # TTL 30s, age 5s → 활성.
        a = resolve_active_sigma(self._sigma(), 5 * self._SEC, 30 * self._SEC)
        assert a.latched is True
        assert a.referent_labels == ('sofa',)

    def test_latched_preserves_absent_flags(self):
        # sigma 의 부분 부재(s3 absent)도 그대로 보존 (latch 는 stale 여부만 판정).
        partial = ParsedSigma(s2=0.9, s3=0.0, s2_absent=False, s3_absent=True,
                              referent_labels=('sofa',), parse_ok=True)
        a = resolve_active_sigma(partial, 2 * self._SEC, 0)
        assert a.latched is True
        assert a.s2_absent is False
        assert a.s3_absent is True

    def test_latched_propagates_s3_structural(self):
        # ADR-0020 D8 — edge sigma 의 s3_structural 이 latch 통과 전파.
        edge = ParsedSigma(s2=0.9, s3=1.0, s2_absent=False, s3_absent=False,
                           referent_labels=('sofa',), parse_ok=True,
                           s3_structural=True)
        a = resolve_active_sigma(edge, 2 * self._SEC, 0)
        assert a.latched is True
        assert a.s3_structural is True

    def test_none_sigma_structural_false(self):
        # 미수신 → 런타임 부재 (구조적 아님).
        a = resolve_active_sigma(None, None, 0)
        assert a.s3_structural is False

    def test_returns_active_sigma_type(self):
        a = resolve_active_sigma(self._sigma(), 1 * self._SEC, 0)
        assert isinstance(a, ActiveSigma)


class TestSanitizeDetectionScore:
    """2026-06-12 세션 34 리뷰 후속 — 비유한 OVD score ingestion 차단.

    비유한값이 compute_g 도메인 검증까지 흘러가면 ValueError → estimator timer
    콜백 사망 경로. ingestion 에서 0.0(보수) 복구 + clamp.
    """

    @pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
    def test_nonfinite_recovers_to_zero(self, bad):
        score, finite = sanitize_detection_score(bad)
        assert score == 0.0
        assert finite is False

    @pytest.mark.parametrize('raw, expected', [
        (0.0, 0.0),
        (1.0, 1.0),
        (0.42, 0.42),
        (-0.3, 0.0),
        (1.7, 1.0),
    ])
    def test_finite_clamped(self, raw, expected):
        score, finite = sanitize_detection_score(raw)
        assert score == expected
        assert finite is True

    def test_output_always_valid_domain(self):
        for raw in [float('nan'), float('inf'), float('-inf'), -5.0, 0.5, 5.0]:
            score, _ = sanitize_detection_score(raw)
            assert 0.0 <= score <= 1.0
            assert math.isfinite(score)


# ---------------------------------------------------------------------------
# resolve_grounded_s1 — grounding 시점 s1 latch (ADR-0029 블로커 2)
# ---------------------------------------------------------------------------

class TestResolveGroundedS1:
    _OK = S1Result(0.7, False, 'ok', 1, 1)
    _STALE = S1Result(0.0, True, 'stale', 0, 0)
    _NODET = S1Result(0.0, True, 'no_detections', 0, 0)

    def test_live_ok_sets_latch(self):
        g, latch = resolve_grounded_s1(self._OK, sigma_active=True,
                                       command_key=100, latch=None)
        assert g.s1 == pytest.approx(0.7)
        assert g.absent is False and g.reason == 'ok'
        assert latch == (0.7, 100, 0, None, 0)

    def test_stale_uses_latch_same_sigma(self):
        # 같은 σ(stamp 100) 에 grounding 이력 → live stale 이어도 latched 유지.
        g, latch = resolve_grounded_s1(self._STALE, sigma_active=True,
                                       command_key=100, latch=(0.7, 100, 0))
        assert g.s1 == pytest.approx(0.7)
        assert g.absent is False and g.reason == 'latched'
        assert latch == (0.7, 100, 0, None, 0)

    def test_stale_no_prior_latch_absent(self):
        # 같은 σ grounding 이력 없음 → live(부재) 그대로.
        g, latch = resolve_grounded_s1(self._STALE, sigma_active=True,
                                       command_key=100, latch=None)
        assert g.absent is True and g.reason == 'stale'
        assert latch is None

    def test_sigma_inactive_preserves_latch(self):
        # 세션 62 — σ 비활성(gate 닫힘 등) tick 은 live passthrough 이되 latch 는
        # *보존* (파기는 referent 변경 시로 한정, PR #297 referent-key 완성).
        g, latch = resolve_grounded_s1(self._STALE, sigma_active=False,
                                       command_key=None, latch=(0.7, 100, 0))
        assert g.absent is True  # latch 미소비 — c 보수 방향
        assert latch == (0.7, 100, 0)  # 보존 — 같은 key 재활성화 시 재개

    def test_new_sigma_discards_old_latch(self):
        # referent 변경(진짜 새 명령, key 100→200) → 옛 latch 즉시 파기 →
        # 재 grounding 전엔 부재.
        g, latch = resolve_grounded_s1(self._STALE, sigma_active=True,
                                       command_key=200, latch=(0.7, 100, 0))
        assert g.absent is True and g.reason == 'stale'
        assert latch is None  # 파기 — 옛 referent 의 s1 이 새 명령에 전이 불가

    def test_new_sigma_regrounds(self):
        # 새 σ 에서 live ok → 새 latch 로 갱신.
        g, latch = resolve_grounded_s1(S1Result(0.4, False, 'ok', 1, 1),
                                       sigma_active=True, command_key=200,
                                       latch=(0.7, 100, 0))
        assert g.s1 == pytest.approx(0.4) and g.reason == 'ok'
        assert latch == (0.4, 200, 0, None, 0)

    def test_freeze_ignores_later_higher_ok_same_sigma(self):
        # 명령 시점 모호(2 후보, s1=0.3) grounding 후, 드론이 한 대상으로 접근해
        # live s1=1.0(1 후보)로 올라도 frozen 유지 — 거짓 해소 차단 (C2).
        g, latch = resolve_grounded_s1(S1Result(0.3, False, 'ok', 2, 2),
                                       True, 100, None)
        assert g.s1 == pytest.approx(0.3) and g.reason == 'ok'
        g, latch = resolve_grounded_s1(S1Result(1.0, False, 'ok', 1, 1),
                                       True, 100, latch)
        assert g.s1 == pytest.approx(0.3)   # frozen — 1.0 으로 안 오름
        assert g.reason == 'latched'
        assert latch == (0.3, 100, 0, None, 0)

    def test_latch_persists_across_multiple_stale_ticks(self):
        latch = None
        g, latch = resolve_grounded_s1(self._OK, True, 100, latch)
        assert g.reason == 'ok'
        for _ in range(5):
            g, latch = resolve_grounded_s1(self._NODET, True, 100, latch)
            assert g.s1 == pytest.approx(0.7) and g.reason == 'latched'

    def test_no_sigma_stamp_passthrough(self):
        # command_key None(σ 미수신) → latch 개념 없음, live 그대로.
        g, latch = resolve_grounded_s1(self._OK, sigma_active=True,
                                       command_key=None, latch=None)
        assert g.reason == 'ok' and g.absent is False
        assert latch is None

    def test_latch_persists_across_sigma_republish_same_referent(self):
        # ADR-0040 Phase 2 — 같은 발화의 σ 가 재발행돼도(stamp 바뀌어도) referent
        # 동일하면 command_key 동일 → latch 유지(360° 스윕 중 grounding 지속).
        key = ('sofa',)
        g, latch = resolve_grounded_s1(self._OK, True, key, None)
        assert g.reason == 'ok' and latch == (0.7, ('sofa',), 0, None, 0)
        # σ 재발행(같은 referent) + 현 tick OVD 끊김 → frozen 유지.
        for _ in range(5):
            g, latch = resolve_grounded_s1(self._NODET, True, key, latch)
            assert g.s1 == pytest.approx(0.7) and g.reason == 'latched'

    def test_latch_resets_on_different_referent(self):
        # 다른 referent(진짜 새 명령) → 재 grounding.
        g, latch = resolve_grounded_s1(self._OK, True, ('sofa',), None)
        assert latch[1] == ('sofa',)
        g, latch = resolve_grounded_s1(S1Result(0.4, False, 'ok', 1, 1),
                                       True, ('cup',), latch)
        assert g.s1 == pytest.approx(0.4) and latch[1] == ('cup',)

    def test_gate_close_reopen_same_referent_resumes_latch(self):
        # 세션 62 — σ 재발행이 inspect 경로를 다시 타면 sigma_bridge 가 gate 를
        # 닫아(sigma_active=False) estimator 에 도달. 같은 referent 의 latch 는
        # 파기되지 않고, gate 재개방(sigma_active=True) 시 frozen s1 이 재개된다.
        key = ('cup',)
        g, latch = resolve_grounded_s1(S1Result(0.01, False, 'ok', 3, 3),
                                       True, key, None)
        assert g.reason == 'ok' and latch[0] == pytest.approx(0.01)
        # gate 닫힘 tick 들 (σ 재발행 → vantage 재비행 대기): latch 보존.
        for _ in range(3):
            g, latch = resolve_grounded_s1(self._STALE, False, None, latch)
            assert g.absent is True          # 미소비 (c 보수 방향)
            assert latch[0] == pytest.approx(0.01)  # 보존
        # gate 재개방 + 같은 referent → frozen s1 재개 (재 grounding 불요).
        g, latch = resolve_grounded_s1(self._NODET, True, key, latch)
        assert g.s1 == pytest.approx(0.01) and g.reason == 'latched'

    def test_gate_close_then_different_referent_discards_latch(self):
        # latch 보존은 referent 불변일 때만 — gate 닫힘 후 *다른* referent 로
        # 재활성화되면 기존 latch 는 반드시 파기된다 (안전 방향 고정).
        g, latch = resolve_grounded_s1(self._OK, True, ('sofa',), None)
        g, latch = resolve_grounded_s1(self._STALE, False, None, latch)
        assert latch is not None  # 비활성 tick 보존
        g, latch = resolve_grounded_s1(self._NODET, True, ('cup',), latch)
        assert g.absent is True and latch is None  # 옛 sofa latch 파기

    # --- 안정 윈도우 (ADR-0038 D2) ---
    _W = 1_500_000_000  # 1.5 s [ns]

    def test_freeze_window_updates_to_lower_s1(self):
        # 윈도우 중 더 모호(s1↓ = 후보 증가) → 갱신. S7 도달 직후 1개(s1=1.0)→2개(s1↓).
        g, latch = resolve_grounded_s1(S1Result(1.0, False, 'ok', 1, 1),
                                       True, 100, None, now_ns=0,
                                       freeze_window_ns=self._W)
        assert g.s1 == pytest.approx(1.0)
        g, latch = resolve_grounded_s1(S1Result(0.3, False, 'ok', 2, 2),
                                       True, 100, latch, now_ns=500_000_000,
                                       freeze_window_ns=self._W)
        assert g.s1 == pytest.approx(0.3) and g.reason == 'latched'

    def test_freeze_window_ignores_higher_s1(self):
        # 윈도우 중에도 s1 상승(드론 접근 거짓 해소)은 무시 — C2 보존.
        g, latch = resolve_grounded_s1(S1Result(0.3, False, 'ok', 2, 2),
                                       True, 100, None, now_ns=0,
                                       freeze_window_ns=self._W)
        g, latch = resolve_grounded_s1(S1Result(1.0, False, 'ok', 1, 1),
                                       True, 100, latch, now_ns=500_000_000,
                                       freeze_window_ns=self._W)
        assert g.s1 == pytest.approx(0.3)

    def test_freeze_window_expired_freezes(self):
        # 윈도우 만료 후엔 더 낮은 s1 도 무시(동결).
        g, latch = resolve_grounded_s1(S1Result(1.0, False, 'ok', 1, 1),
                                       True, 100, None, now_ns=0,
                                       freeze_window_ns=self._W)
        g, latch = resolve_grounded_s1(S1Result(0.3, False, 'ok', 2, 2),
                                       True, 100, latch, now_ns=2_000_000_000,
                                       freeze_window_ns=self._W)
        assert g.s1 == pytest.approx(1.0)  # 윈도우 만료 → frozen


class TestMinRuleDebounce:
    """ADR-0040 D8 (세션 61) — min-rule 시간 debounce.

    OVD 단일프레임 중복박스 아티팩트(s1 1프레임 붕괴)가 latch 를 poison 못 하게,
    더 낮은 s1 갱신을 min_persist_frames 연속 'ok' 후에만 반영.
    """
    _W = 10_000_000_000  # 10 s 윈도우 [ns]

    def _ok(self, s1, nm=1):
        return S1Result(s1, False, 'ok', nm, nm)

    def test_single_frame_low_does_not_poison(self):
        # 첫 ok s1=1.0 → 단일 저-s1 프레임(아티팩트) → frozen 유지(반영 안 됨).
        g, latch = resolve_grounded_s1(self._ok(1.0), True, 100, None,
                                       now_ns=0, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        assert g.s1 == pytest.approx(1.0)
        g, latch = resolve_grounded_s1(self._ok(0.01, 2), True, 100, latch,
                                       now_ns=1, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        assert g.s1 == pytest.approx(1.0)  # debounce — 1프레임으론 반영 안 함
        # 깨끗한 상위 관측이 오면 streak 리셋(아티팩트 종료) → 여전히 1.0.
        g, latch = resolve_grounded_s1(self._ok(1.0), True, 100, latch,
                                       now_ns=2, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        assert g.s1 == pytest.approx(1.0)
        # 또 단일 저프레임 → 다시 count=1 → 미반영.
        g, latch = resolve_grounded_s1(self._ok(0.01, 2), True, 100, latch,
                                       now_ns=3, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        assert g.s1 == pytest.approx(1.0)

    def test_persistent_low_commits(self):
        # 지속적 모호(연속 저-s1 3프레임) → 3번째에서 반영(진짜 모호성, S5/S7).
        g, latch = resolve_grounded_s1(self._ok(1.0), True, 100, None,
                                       now_ns=0, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        for i, expect in ((1, 1.0), (2, 1.0), (3, 0.2)):
            g, latch = resolve_grounded_s1(self._ok(0.2, 2), True, 100, latch,
                                           now_ns=i, freeze_window_ns=self._W,
                                           min_persist_frames=3)
            assert g.s1 == pytest.approx(expect)
        assert g.reason == 'latched'

    def test_absent_frames_neutral_streak_preserved(self):
        # 저프레임 사이의 부재(OVD 끊김)는 중립 — streak 리셋 안 함.
        g, latch = resolve_grounded_s1(self._ok(1.0), True, 100, None,
                                       now_ns=0, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        g, latch = resolve_grounded_s1(self._ok(0.2, 2), True, 100, latch,
                                       now_ns=1, freeze_window_ns=self._W,
                                       min_persist_frames=3)  # count=1
        # 부재(no_detections) — 중립.
        g, latch = resolve_grounded_s1(S1Result(0.0, True, 'no_detections', 0, 0),
                                       True, 100, latch, now_ns=2,
                                       freeze_window_ns=self._W, min_persist_frames=3)
        assert g.s1 == pytest.approx(1.0)
        g, latch = resolve_grounded_s1(self._ok(0.2, 2), True, 100, latch,
                                       now_ns=3, freeze_window_ns=self._W,
                                       min_persist_frames=3)  # count=2
        g, latch = resolve_grounded_s1(self._ok(0.2, 2), True, 100, latch,
                                       now_ns=4, freeze_window_ns=self._W,
                                       min_persist_frames=3)  # count=3 → commit
        assert g.s1 == pytest.approx(0.2)

    def test_commits_minimum_over_streak(self):
        # streak 동안 가장 낮은(가장 모호) 값으로 commit.
        g, latch = resolve_grounded_s1(self._ok(1.0), True, 100, None,
                                       now_ns=0, freeze_window_ns=self._W,
                                       min_persist_frames=3)
        for i, s1v in ((1, 0.3), (2, 0.1), (3, 0.2)):
            g, latch = resolve_grounded_s1(self._ok(s1v, 2), True, 100, latch,
                                           now_ns=i, freeze_window_ns=self._W,
                                           min_persist_frames=3)
        assert g.s1 == pytest.approx(0.1)  # min(0.3,0.1,0.2)

    def test_default_immediate_backcompat(self):
        # min_persist_frames=1(기본) → 종전대로 첫 저프레임 즉시 반영.
        g, latch = resolve_grounded_s1(self._ok(1.0), True, 100, None,
                                       now_ns=0, freeze_window_ns=self._W)
        g, latch = resolve_grounded_s1(self._ok(0.2, 2), True, 100, latch,
                                       now_ns=1, freeze_window_ns=self._W)
        assert g.s1 == pytest.approx(0.2)  # 즉시
