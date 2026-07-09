"""intent_llm.classifier 단위 테스트.

ClassifierWrapper — closed-vocabulary keyword matching 측 결정성 + 5 스킬
별 trigger + entropy + ask_user fallback 검증.
"""

from __future__ import annotations

import pytest

from intent_llm.classifier import (
    CATEGORY,
    IDENTIFIER,
    ClassifierWrapper,
)
from intent_llm.interface import (
    SIGNAL_ENTROPY,
    SIGNAL_LOGPROB,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    IntentWrapper,
)
from intent_llm.skill_catalog import ALL_SKILLS, SkillName


# uniform 분포 측 confidence — ALL_SKILLS 측 derive (PR #124 review
# M-4 정정 — magic value '0.2' literal 회피).
_NUM_SKILLS = len(ALL_SKILLS)
_UNIFORM_CONFIDENCE = 1.0 / _NUM_SKILLS


class TestWrapperContract:
    def test_category_classifier(self) -> None:
        assert CATEGORY == 'classifier'

    def test_identifier_closed_vocab(self) -> None:
        assert IDENTIFIER == 'closed-vocabulary-keyword'

    def test_instance_satisfies_protocol(self) -> None:
        """ClassifierWrapper 측 IntentWrapper Protocol 충족."""
        wrapper = ClassifierWrapper()
        assert isinstance(wrapper, IntentWrapper)

    def test_returns_intent_result(self) -> None:
        wrapper = ClassifierWrapper()
        inp = IntentInput(utterance='가 줘', scenario_id='S5')
        result = wrapper.process(inp)
        assert isinstance(result, IntentResult)


class TestSkillTriggers:
    """각 5 스킬 측 keyword trigger — argmax skill 정합."""

    def _process(self, utterance: str) -> IntentResult:
        return ClassifierWrapper().process(
            IntentInput(utterance=utterance, scenario_id='S5')
        )

    def test_move_to_korean(self) -> None:
        r = self._process('저쪽으로 가 줘')
        assert r.typed_action.skill == SkillName.MOVE_TO

    def test_move_to_english(self) -> None:
        r = self._process('Please move forward')
        assert r.typed_action.skill == SkillName.MOVE_TO

    def test_inspect_korean(self) -> None:
        r = self._process('저거 살펴봐')
        assert r.typed_action.skill == SkillName.INSPECT

    def test_inspect_english(self) -> None:
        r = self._process('Inspect that object')
        assert r.typed_action.skill == SkillName.INSPECT

    def test_return_to_dock(self) -> None:
        r = self._process('Return to dock please')
        assert r.typed_action.skill == SkillName.RETURN_TO_DOCK

    def test_emergency_land(self) -> None:
        r = self._process('Emergency stop')
        assert r.typed_action.skill == SkillName.EMERGENCY_LAND

    def test_ask_user_question_mark(self) -> None:
        r = self._process('what should I do?')
        assert r.typed_action.skill == SkillName.ASK_USER


class TestEntropy:
    def test_clear_command_high_confidence(self) -> None:
        """명확한 발화 측 skill 식별 + confidence 측 *높음*.

        (s1 접지 엔트로피는 OVD 전용 — classifier 측 미산출, §2.1.)
        """
        wrapper = ClassifierWrapper()
        # 'go' + 'move' 측 move_to 측 강한 신호.
        result = wrapper.process(
            IntentInput(utterance='Please move and go forward', scenario_id='S5')
        )
        assert result.typed_action.skill == SkillName.MOVE_TO
        # uniform(1/5)보다 강한 신호 → confidence 높음.
        assert result.confidence_raw > _UNIFORM_CONFIDENCE

    def test_no_match_ask_user_fallback(self) -> None:
        """매치 0 측 ASK_USER fallback + c_raw=0.0.

        PR #124 review C-1 정정 정합 — 매치 0 측 *명시적 ASK_USER* (이전 measure
        uniform 분포 argmax tie-break 측 ALL_SKILLS[0]=MOVE_TO 반환 = safety
        위반). ADR-0013 D4 c_lo=0.4 미만 측 Tier 2 ask 자동 trigger 정합.
        (s1 은 OVD 전용 — classifier 미산출, §2.1.)
        """
        wrapper = ClassifierWrapper()
        # 'asdfgh' 측 어떤 keyword 측 매치 X.
        result = wrapper.process(
            IntentInput(utterance='asdfgh xyzqw', scenario_id='S5')
        )
        # 1. skill identity — *명시적 ASK_USER* (PR #124 C-1 정정 핵심).
        assert result.typed_action.skill == SkillName.ASK_USER
        # 2. confidence_raw = 0.0 — Tier 2 c_lo=0.4 미만 ask 자동 trigger.
        assert result.confidence_raw == 0.0
        # 3. s1 키 부재 (OVD 전용).
        assert SIGNAL_ENTROPY not in result.signals

    def test_partial_match_uses_uniform_confidence_formula(self) -> None:
        """matching score 측 *동률* 측 softmax 측 uniform 분포 — confidence 측
        1/N_SKILLS (N=5) = _UNIFORM_CONFIDENCE.

        본 test 측 *매치 0 fallback* 과 *동률* 측 구분 — 매치 ≥ 1 측 softmax 경로,
        매치 0 측 ASK_USER 경로 (PR #124 C-1 정정).
        """
        wrapper = ClassifierWrapper()
        # 모든 5 카테고리 keyword 한 번씩 — equal scores → softmax uniform.
        # '가서' (MOVE_TO), '봐' (INSPECT), '돌아' (RETURN_TO_DOCK),
        # 'land' (EMERGENCY_LAND), '?' (ASK_USER) — 각 카테고리 매치 1 회씩.
        result = wrapper.process(
            IntentInput(utterance='가서 봐 돌아 land ?', scenario_id='S5')
        )
        # 매치 ≥ 1 측 softmax 경로 → uniform 분포 (equal scores).
        # argmax tie-break 측 첫 index = MOVE_TO (구현 의존).
        assert result.confidence_raw == pytest.approx(
            _UNIFORM_CONFIDENCE, rel=1e-6
        )


class TestSignals:
    def test_no_canonical_signals(self) -> None:
        """classifier 측 정본 신호 미산출 — s1 키 부재(OVD 전용), s2/ℓ 측 None
        (deterministic). §2.1 정합."""
        wrapper = ClassifierWrapper()
        result = wrapper.process(IntentInput(utterance='가 줘', scenario_id='S5'))
        assert SIGNAL_ENTROPY not in result.signals
        assert result.signals[SIGNAL_SELF_CONSISTENCY] is None
        assert result.signals[SIGNAL_LOGPROB] is None


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        """동일 utterance 측 동일 IntentResult — *deterministic* wrapper."""
        wrapper = ClassifierWrapper()
        inp = IntentInput(utterance='저거 살펴봐 줘', scenario_id='S5')
        r1 = wrapper.process(inp)
        r2 = wrapper.process(inp)
        assert r1 == r2

    def test_context_graph_ignored(self) -> None:
        """closed-vocabulary 측 context_graph 측 *무시* — 발화 만 사용."""
        wrapper = ClassifierWrapper()
        inp_no = IntentInput(utterance='가 줘', scenario_id='S5')
        inp_ctx = IntentInput(
            utterance='가 줘',
            scenario_id='S5',
            context_graph={'objects': []},
        )
        r_no = wrapper.process(inp_no)
        r_ctx = wrapper.process(inp_ctx)
        assert r_no == r_ctx


class TestConfidenceRange:
    def test_confidence_in_unit_interval(self) -> None:
        """모든 utterance 측 confidence_raw ∈ [0, 1]."""
        wrapper = ClassifierWrapper()
        for utterance in (
            '가 줘',
            '저기 살펴',
            '복귀',
            '비상 착륙',
            'what?',
            'random garbage 12345',
            '가 줘 살펴 돌아 land what',  # 모든 카테고리 트리거
        ):
            result = wrapper.process(
                IntentInput(utterance=utterance, scenario_id='S5')
            )
            assert 0.0 <= result.confidence_raw <= 1.0


class TestArgsPlaceholder:
    def test_args_empty_dict(self) -> None:
        """stub 측 args 측 placeholder — 본 PR scope (B7 #12 분할 2b-1) 측
        skill identification 만.
        """
        wrapper = ClassifierWrapper()
        result = wrapper.process(IntentInput(utterance='가 줘', scenario_id='S5'))
        assert result.typed_action.args == {}
