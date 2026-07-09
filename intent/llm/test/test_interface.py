"""intent_llm.interface 단위 테스트.

TypedAction · IntentInput · IntentResult · IntentWrapper Protocol 정합 검증.
"""

from __future__ import annotations

import pytest

from intent_llm.interface import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    SIGNAL_ENTROPY,
    SIGNAL_LOGPROB,
    SIGNAL_S3_CAPABILITY,
    SIGNAL_SELF_CONSISTENCY,
    VALID_SIGNAL_KEYS,
    IntentInput,
    IntentResult,
    IntentWrapper,
    TypedAction,
)
from intent_llm.skill_catalog import SkillName


class TestConstants:
    def test_confidence_range(self) -> None:
        """raw confidence 측 단위 구간 $[0, 1]$."""
        assert CONFIDENCE_MIN == 0.0
        assert CONFIDENCE_MAX == 1.0

    def test_signal_keys_distinct(self) -> None:
        """3 signal key 측 distinct — cmsm-proof §2.1 (H, ρ, ℓ) 정합."""
        keys = {SIGNAL_ENTROPY, SIGNAL_SELF_CONSISTENCY, SIGNAL_LOGPROB}
        assert len(keys) == 3


class TestTypedAction:
    def test_constructs(self) -> None:
        a = TypedAction(skill=SkillName.MOVE_TO, args={'position': (1.0, 2.0, 3.0)})
        assert a.skill == SkillName.MOVE_TO
        assert a.args['position'] == (1.0, 2.0, 3.0)

    def test_default_args_empty(self) -> None:
        a = TypedAction(skill=SkillName.RETURN_TO_DOCK)
        assert a.args == {}

    def test_invalid_skill_type(self) -> None:
        with pytest.raises(TypeError, match='skill'):
            TypedAction(skill='move_to')  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        a = TypedAction(skill=SkillName.MOVE_TO)
        with pytest.raises((AttributeError, Exception)):
            a.skill = SkillName.INSPECT  # type: ignore[misc]


class TestIntentInput:
    def test_constructs_text_only(self) -> None:
        inp = IntentInput(utterance='저쪽으로 가 줘', scenario_id='S5')
        assert inp.utterance == '저쪽으로 가 줘'
        assert inp.scenario_id == 'S5'
        assert inp.context_graph is None

    def test_constructs_with_context_graph(self) -> None:
        graph = {'objects': [{'id': 'cup_1', 'pos': (0.3, 0.5, 0.8)}]}
        inp = IntentInput(
            utterance='저거 봐 줘',
            scenario_id='S5',
            context_graph=graph,
        )
        assert inp.context_graph == graph

    def test_empty_utterance_rejected(self) -> None:
        with pytest.raises(ValueError, match='utterance'):
            IntentInput(utterance='', scenario_id='S5')

    def test_whitespace_only_utterance_rejected(self) -> None:
        with pytest.raises(ValueError, match='utterance'):
            IntentInput(utterance='   ', scenario_id='S5')

    def test_empty_scenario_rejected(self) -> None:
        with pytest.raises(ValueError, match='scenario_id'):
            IntentInput(utterance='가 줘', scenario_id='')

    def test_frozen(self) -> None:
        inp = IntentInput(utterance='가 줘', scenario_id='S5')
        with pytest.raises((AttributeError, Exception)):
            inp.utterance = 'changed'  # type: ignore[misc]


class TestIntentResult:
    def _action(self) -> TypedAction:
        return TypedAction(skill=SkillName.MOVE_TO)

    def test_constructs(self) -> None:
        r = IntentResult(
            typed_action=self._action(),
            confidence_raw=0.85,
            signals={SIGNAL_ENTROPY: 0.5},
        )
        assert r.confidence_raw == 0.85
        assert r.signals[SIGNAL_ENTROPY] == 0.5

    def test_confidence_boundary_zero(self) -> None:
        r = IntentResult(typed_action=self._action(), confidence_raw=0.0)
        assert r.confidence_raw == 0.0

    def test_confidence_boundary_one(self) -> None:
        r = IntentResult(typed_action=self._action(), confidence_raw=1.0)
        assert r.confidence_raw == 1.0

    def test_confidence_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match='confidence_raw'):
            IntentResult(typed_action=self._action(), confidence_raw=-0.01)

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match='confidence_raw'):
            IntentResult(typed_action=self._action(), confidence_raw=1.01)

    def test_confidence_bool_rejected(self) -> None:
        """bool 측 int subclass 이나 confidence 측 명시적 거부."""
        with pytest.raises(TypeError, match='confidence_raw'):
            IntentResult(typed_action=self._action(), confidence_raw=True)  # type: ignore[arg-type]

    def test_invalid_typed_action_type(self) -> None:
        with pytest.raises(TypeError, match='typed_action'):
            IntentResult(typed_action='move_to', confidence_raw=0.5)  # type: ignore[arg-type]

    def test_signals_default_empty(self) -> None:
        r = IntentResult(typed_action=self._action(), confidence_raw=0.5)
        assert r.signals == {}

    def test_unknown_signal_key_rejected(self) -> None:
        """PR #124 review M-1 — VALID_SIGNAL_KEYS 외 키 측 typo 차단."""
        with pytest.raises(ValueError, match='signals'):
            IntentResult(
                typed_action=self._action(),
                confidence_raw=0.5,
                signals={'s1_entrpy': 0.5},  # typo
            )

    def test_partial_signals_accepted(self) -> None:
        """일부 신호 측 부재 OK — classifier 측 H 만 채움 케이스."""
        r = IntentResult(
            typed_action=self._action(),
            confidence_raw=0.5,
            signals={SIGNAL_ENTROPY: 0.5},
        )
        assert r.signals[SIGNAL_ENTROPY] == 0.5

    def test_none_signal_value_accepted(self) -> None:
        """value 측 None OK — 키 측 valid 면 wrapper 측 None 표시 (미지원 신호)."""
        r = IntentResult(
            typed_action=self._action(),
            confidence_raw=0.5,
            signals={
                SIGNAL_ENTROPY: 0.5,
                SIGNAL_SELF_CONSISTENCY: None,
                SIGNAL_LOGPROB: None,
            },
        )
        assert r.signals[SIGNAL_SELF_CONSISTENCY] is None


class TestValidSignalKeys:
    def test_contains_signal_keys(self) -> None:
        # ADR-0020 D8 — s3_capability (구조적 능력 플래그) 추가.
        assert VALID_SIGNAL_KEYS == {
            SIGNAL_ENTROPY, SIGNAL_SELF_CONSISTENCY, SIGNAL_LOGPROB,
            SIGNAL_S3_CAPABILITY,
        }

    def test_frozen(self) -> None:
        """frozenset 측 mutation 불가 — single source-of-truth 측 보호."""
        with pytest.raises(AttributeError):
            VALID_SIGNAL_KEYS.add('x')  # type: ignore[attr-defined]


class TestIntentWrapperProtocol:
    """runtime_checkable Protocol — duck-typing isinstance check 정합."""

    def test_minimal_compliant_class(self) -> None:
        class MyWrapper:
            category = 'test'
            identifier = 'test-1'

            def process(self, intent_input: IntentInput) -> IntentResult:
                return IntentResult(
                    typed_action=TypedAction(skill=SkillName.ASK_USER),
                    confidence_raw=0.0,
                )

        assert isinstance(MyWrapper(), IntentWrapper)

    def test_missing_process_not_compliant(self) -> None:
        class NotAWrapper:
            category = 'test'
            identifier = 'test-x'

        assert not isinstance(NotAWrapper(), IntentWrapper)
