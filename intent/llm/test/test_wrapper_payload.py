"""intent_llm.wrapper_payload 단위 테스트 — wrapper_node 순수 로직.

IntentInput 빌드 + IntentResult 직렬화 + context 파싱 + fault/safety-side 계약
(sigma/theta) 호환 검증. host venv pytest (rclpy 무관).
"""

from __future__ import annotations

import json

import pytest

from intent_llm import wrapper_payload as wp
from intent_llm.interface import (
    SIGNAL_ENTROPY,
    SIGNAL_LOGPROB,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    TypedAction,
)
from intent_llm.skill_catalog import SkillName


def _result(skill=SkillName.MOVE_TO, args=None, c=0.8, signals=None) -> IntentResult:
    return IntentResult(
        typed_action=TypedAction(skill=skill, args=args or {}),
        confidence_raw=c,
        signals=signals or {},
    )


class TestBuildIntentInput:
    def test_direct_mode_none_context(self) -> None:
        ii = wp.build_intent_input('창문 쪽으로 가줘', 'S5')
        assert isinstance(ii, IntentInput)
        assert ii.utterance == '창문 쪽으로 가줘'
        assert ii.scenario_id == 'S5'
        assert ii.context_graph is None

    def test_fusion_mode_with_context(self) -> None:
        ctx = {'objects': ['sofa', 'tv']}
        ii = wp.build_intent_input('저거 봐줘', 'S7', ctx)
        assert ii.context_graph == ctx

    def test_empty_utterance_raises(self) -> None:
        with pytest.raises(ValueError):
            wp.build_intent_input('  ', 'S5')


class TestTypedActionPayload:
    def test_sigma_theta_keys(self) -> None:
        """fault/safety-side 계약 — skill→sigma, args→theta."""
        payload = wp.typed_action_payload(
            TypedAction(skill=SkillName.MOVE_TO, args={'position': [1, 2, 3]})
        )
        assert payload == {'sigma': 'move_to', 'theta': {'position': [1, 2, 3]}}

    def test_empty_args(self) -> None:
        payload = wp.typed_action_payload(TypedAction(skill=SkillName.RETURN_TO_DOCK))
        assert payload == {'sigma': 'return_to_dock', 'theta': {}}


class TestResultPayload:
    def test_includes_c_and_signals(self) -> None:
        payload = wp.result_payload(_result(
            c=0.73,
            signals={SIGNAL_ENTROPY: 0.2, SIGNAL_SELF_CONSISTENCY: 1.0,
                     SIGNAL_LOGPROB: -1.5},
        ))
        assert payload['sigma'] == 'move_to'
        assert payload['c'] == 0.73
        assert payload['signals'][SIGNAL_ENTROPY] == 0.2
        assert payload['signals'][SIGNAL_LOGPROB] == -1.5

    def test_signals_may_be_partial(self) -> None:
        payload = wp.result_payload(_result(signals={SIGNAL_ENTROPY: 0.5}))
        assert payload['signals'] == {SIGNAL_ENTROPY: 0.5}


class TestSerializeResult:
    def test_round_trip(self) -> None:
        s = wp.serialize_result(_result(
            skill=SkillName.INSPECT, args={'target_id': 'sofa'}, c=0.6,
            signals={SIGNAL_SELF_CONSISTENCY: 0.667},
        ))
        data = json.loads(s)
        assert data['sigma'] == 'inspect'
        assert data['theta'] == {'target_id': 'sofa'}
        assert data['c'] == 0.6
        assert data['signals'][SIGNAL_SELF_CONSISTENCY] == 0.667

    def test_ask_user_fallback_serializes(self) -> None:
        s = wp.serialize_result(_result(skill=SkillName.ASK_USER, c=0.0))
        data = json.loads(s)
        assert data['sigma'] == 'ask_user'
        assert data['c'] == 0.0

    def test_korean_args_not_escaped(self) -> None:
        s = wp.serialize_result(_result(
            skill=SkillName.ASK_USER, c=0.0,
            args={'question': '어디로 갈까요?'},
        ))
        assert '어디로' in s  # ensure_ascii=False


class TestInjectorContractCompat:
    """출력 payload 가 injector hallucination 입력 계약(sigma/theta)과 호환."""

    def test_payload_has_sigma_theta_for_injector(self) -> None:
        # eval_faults.injector_helpers.typed_action_from_json 는 data['sigma'],
        # data['theta'] 를 읽음 — 본 payload 가 그 키를 포함해야 호환.
        data = json.loads(wp.serialize_result(_result(
            skill=SkillName.MOVE_TO, args={'position': [0, 0, 1]},
        )))
        assert 'sigma' in data and 'theta' in data
        assert data['sigma'] in {s.value for s in SkillName}
        assert isinstance(data['theta'], dict)


class TestParseContextGraph:
    def test_valid_dict(self) -> None:
        assert wp.parse_context_graph('{"a": 1}') == {'a': 1}

    def test_none(self) -> None:
        assert wp.parse_context_graph(None) is None

    def test_empty_string(self) -> None:
        assert wp.parse_context_graph('   ') is None

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError):
            wp.parse_context_graph('[1, 2, 3]')

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(ValueError):  # json.JSONDecodeError ⊂ ValueError
            wp.parse_context_graph('{not json}')


# ---------------------------------------------------------------------------
# ADR-0029 블로커 1 — referent class 주입 (인스턴스 id → OVD 클래스)
# ---------------------------------------------------------------------------

_CTX = {
    'objects': [
        {'name': 'chair_left', 'position': [2.0, -0.4, 0.4], 'ovd_class': 'chair'},
        {'name': 'sofa', 'position': [-1.8, 1.5, 0.4], 'ovd_class': 'sofa'},
        {'name': 'tv_stand', 'position': [-1.8, -1.5, 0.3], 'ovd_class': None},
    ],
}


class TestReferentClassFor:
    def test_instance_resolves_to_class(self) -> None:
        assert wp.referent_class_for('chair_left', _CTX) == 'chair'

    def test_generic_name_resolves_to_itself(self) -> None:
        assert wp.referent_class_for('sofa', _CTX) == 'sofa'

    def test_vocab_absent_object_returns_none(self) -> None:
        assert wp.referent_class_for('tv_stand', _CTX) is None

    def test_unknown_target_returns_none(self) -> None:
        assert wp.referent_class_for('lamp', _CTX) is None

    def test_no_context_returns_none(self) -> None:
        assert wp.referent_class_for('chair_left', None) is None

    def test_no_target_returns_none(self) -> None:
        assert wp.referent_class_for(None, _CTX) is None


class TestSerializeResultWithContext:
    def test_injects_target_class(self) -> None:
        r = _result(skill=SkillName.INSPECT, args={'target_id': 'chair_left'})
        payload = json.loads(wp.serialize_result_with_context(r, _CTX))
        assert payload['theta']['target_id'] == 'chair_left'
        assert payload['theta']['target_class'] == 'chair'

    def test_no_context_omits_class(self) -> None:
        r = _result(skill=SkillName.INSPECT, args={'target_id': 'chair_left'})
        payload = json.loads(wp.serialize_result_with_context(r, None))
        assert 'target_class' not in payload['theta']

    def test_vocab_absent_omits_class(self) -> None:
        r = _result(skill=SkillName.INSPECT, args={'target_id': 'tv_stand'})
        payload = json.loads(wp.serialize_result_with_context(r, _CTX))
        assert 'target_class' not in payload['theta']

    def test_preserves_sigma_c_signals(self) -> None:
        # 기존 키 불변 — 다운스트림 호환 (injector·gate).
        r = _result(skill=SkillName.INSPECT, args={'target_id': 'sofa'}, c=0.7)
        payload = json.loads(wp.serialize_result_with_context(r, _CTX))
        assert payload['sigma'] == 'inspect'
        assert payload['c'] == pytest.approx(0.7)
        assert 'signals' in payload
        assert payload['theta']['target_class'] == 'sofa'
