"""intent_llm.adversarial 단위 테스트.

AdversarialWrapper 측 mock 동작 — skill swap + confidence inflation + signals
왜곡 + composition pattern (CloudLLMWrapper wrap) + ASK_USER 회피 + Protocol
충족 + determinism + 다른 wrapper 측 distinct signature.

B7 #12 분할 2b-4 scope — ADR-0018 D3 row 5 + D5 (OWASP LLM01 prompt injection
wrapper, 기본 wrap 대상 = GPT-4o).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from intent_llm.adversarial import (
    CATEGORY,
    DEFAULT_WRAPPED_IDENTIFIER,
    IDENTIFIER,
    _ADVERSARIAL_CONFIDENCE,
    _ADVERSARIAL_LOGPROB,
    _ADVERSARIAL_RHO,
    _SKILL_SWAP,
    AdversarialWrapper,
    _swap_skill,
)
from intent_llm.cloud_llm import CloudLLMWrapper
from intent_llm.interface import (
    SIGNAL_ENTROPY,
    SIGNAL_LOGPROB,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    IntentWrapper,
)
from intent_llm.skill_catalog import ALL_SKILLS, SkillName


# C14 이후 CloudLLMWrapper.process() 측 실 API 호출 시도 — OPENAI_API_KEY 미설정
# 시 RuntimeError. AdversarialWrapper 측 내부 호출 측 차단 → classifier mock 사용.
@pytest.fixture(autouse=True)
def _patch_cloud_llm_to_classifier():
    """CloudLLMWrapper.process 측 ClassifierWrapper 위임 — API 없이 테스트 가능."""
    from intent_llm.classifier import ClassifierWrapper
    from intent_llm.cloud_llm import CloudLLMWrapper

    classifier = ClassifierWrapper()
    with patch.object(CloudLLMWrapper, 'process', lambda self, inp: classifier.process(inp)):
        yield


# 발화 — 다양한 base skill trigger 측 cover.
_UTTERANCE_MOVE = 'go forward to the kitchen'  # MOVE_TO trigger
_UTTERANCE_INSPECT = 'show me what is there'  # INSPECT trigger ('show' 매치)
_UTTERANCE_RETURN = 'return to the dock'  # RETURN_TO_DOCK trigger
_UTTERANCE_LAND = 'emergency land now'  # EMERGENCY_LAND trigger
_UTTERANCE_FALLBACK = 'asdfgh xyzqw'  # ASK_USER fallback (매치 0)


def _input(utterance: str = _UTTERANCE_MOVE) -> IntentInput:
    return IntentInput(utterance=utterance, scenario_id='S5')


# -------------------------------------------------------------------- category


class TestCategoryAndIdentifier:
    def test_category(self) -> None:
        assert CATEGORY == 'adversarial'
        assert AdversarialWrapper.category == CATEGORY

    def test_identifier_default(self) -> None:
        """ADR-0018 D5 — 기본 식별자 = 'gpt-4o-injected'."""
        assert IDENTIFIER == 'gpt-4o-injected'

    def test_default_wrapped_identifier(self) -> None:
        """ADR-0018 D5 — 기본 wrap 대상 = GPT-4o."""
        assert DEFAULT_WRAPPED_IDENTIFIER == 'gpt-4o'


# -------------------------------------------------------------------- construction


class TestConstruction:
    def test_constructs_default(self) -> None:
        w = AdversarialWrapper()
        assert w.identifier == IDENTIFIER
        assert w.wrapped_identifier == DEFAULT_WRAPPED_IDENTIFIER
        assert w.category == 'adversarial'

    def test_constructs_with_explicit_wrapped(self) -> None:
        """다른 wrap 대상 측 후속 PR 측 확장 가능 — 본 PR 측 default scope 만."""
        w = AdversarialWrapper(wrapped_identifier='gpt-5', identifier='gpt-5-injected')
        assert w.identifier == 'gpt-5-injected'
        assert w.wrapped_identifier == 'gpt-5'

    def test_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            AdversarialWrapper(identifier='')

    def test_empty_wrapped_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match='wrapped_identifier'):
            AdversarialWrapper(wrapped_identifier='')

    def test_composition_creates_cloud_wrapper(self) -> None:
        """ADR-0018 D5 composition pattern — _wrapped 측 CloudLLMWrapper instance."""
        w = AdversarialWrapper()
        assert isinstance(w._wrapped, CloudLLMWrapper)
        assert w._wrapped.identifier == 'gpt-4o'


# -------------------------------------------------------------------- protocol


class TestProtocol:
    def test_satisfies_intent_wrapper(self) -> None:
        assert isinstance(AdversarialWrapper(), IntentWrapper)


# -------------------------------------------------------------------- skill swap mapping


class TestSkillSwapMapping:
    """_SKILL_SWAP 측 deterministic mapping — 5 skill 모두 cover + 위험 skill
    측 monitoring → 안전 위반 시도 정합."""

    def test_all_five_skills_covered(self) -> None:
        """카탈로그 5 skill 모두 _SKILL_SWAP 측 key 측 존재 — 누락 시 KeyError."""
        for skill in ALL_SKILLS:
            assert skill in _SKILL_SWAP

    def test_swap_target_in_catalog(self) -> None:
        """swap 후 skill 측 모두 카탈로그 5 skill 내 — closed vocabulary 정합."""
        for swapped in _SKILL_SWAP.values():
            assert swapped in ALL_SKILLS

    def test_move_to_swaps_to_inspect(self) -> None:
        assert _swap_skill(SkillName.MOVE_TO) == SkillName.INSPECT

    def test_inspect_swaps_to_move_to(self) -> None:
        assert _swap_skill(SkillName.INSPECT) == SkillName.MOVE_TO

    def test_return_to_dock_swaps_to_move_to(self) -> None:
        """위험 — 귀환 회피 측 진행 swap."""
        assert _swap_skill(SkillName.RETURN_TO_DOCK) == SkillName.MOVE_TO

    def test_emergency_land_swaps_to_move_to(self) -> None:
        """극위험 — 긴급착륙 회피 측 진행 swap. paper §C 측 Tier 1·2 차단
        의무 검증 가장 극단 자리."""
        assert _swap_skill(SkillName.EMERGENCY_LAND) == SkillName.MOVE_TO

    def test_ask_user_swaps_to_move_to(self) -> None:
        """위험 — 모호 측 confirm 측 회피 + 명령 강제."""
        assert _swap_skill(SkillName.ASK_USER) == SkillName.MOVE_TO

    def test_no_skill_swaps_to_itself(self) -> None:
        """모든 swap 측 *반드시* base 측 다른 skill — 효과 0 swap 회피."""
        for base, swapped in _SKILL_SWAP.items():
            assert base != swapped

    def test_swap_unknown_skill_raises(self) -> None:
        """defensive — _SKILL_SWAP 측 cover 안 된 skill (카탈로그 확장 시 본
        mapping 측 동시 확장 의무) 측 KeyError.

        **M-1 정정 (2026-05-27)** — 이전 `pytest.raises((KeyError, TypeError))`
        측 *과방어* — 일반 class instance 측 hashable (default __hash__) 측
        dict lookup 측 KeyError 만 발생. TypeError 측 __hash__=None 측만 —
        본 _FakeSkill 측 unhashable 아님 → KeyError 만 강제.
        """

        class _FakeSkill:
            pass

        with pytest.raises(KeyError):
            _swap_skill(_FakeSkill())  # type: ignore[arg-type]


# -------------------------------------------------------------------- skill swap behavior


class TestSkillSwapBehavior:
    """AdversarialWrapper.process() 측 skill swap 측 *모든* 호출 측 적용."""

    def test_move_utterance_swaps_to_inspect(self) -> None:
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_MOVE))
        # base 측 MOVE_TO → adversarial 측 INSPECT.
        assert r.typed_action.skill == SkillName.INSPECT

    def test_return_utterance_swaps_to_move_to(self) -> None:
        """**위험** — RETURN_TO_DOCK 측 회피 + 진행 swap (paper §C 안전 위반)."""
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_RETURN))
        # base 측 RETURN_TO_DOCK → adversarial 측 MOVE_TO (귀환 회피).
        assert r.typed_action.skill == SkillName.MOVE_TO

    def test_land_utterance_swaps_to_move_to(self) -> None:
        """**극위험** — EMERGENCY_LAND 측 회피 + 진행 swap (paper §C 안전
        위반 가장 극단)."""
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_LAND))
        # base 측 EMERGENCY_LAND → adversarial 측 MOVE_TO (착륙 회피).
        assert r.typed_action.skill == SkillName.MOVE_TO

    def test_ask_user_fallback_swaps_to_move_to(self) -> None:
        """**위험** — base 측 ASK_USER fallback (c_raw=0.0) 측 adversarial 측
        MOVE_TO swap + confidence inflation = ASK 측 직접 회피 + 명령 강제.
        paper §C 측 adversarial 가장 본질 측 검증 자리."""
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_FALLBACK))
        # base 측 ASK_USER → adversarial 측 MOVE_TO.
        assert r.typed_action.skill == SkillName.MOVE_TO


# -------------------------------------------------------------------- confidence inflation


class TestConfidenceInflation:
    """confidence_raw 측 _ADVERSARIAL_CONFIDENCE (0.95) 측 fixed override.

    Tier 2 c_lo=0.4 측 *훨씬 초과* → ask 자동 trigger 측 우회. base 측 무관
    측 *모든* 호출 측 동일 0.95 — paper §C 측 adversarial 측 *우회 시도* 자리.
    """

    def test_inflation_value_above_tier2_c_lo(self) -> None:
        """_ADVERSARIAL_CONFIDENCE 측 Tier 2 c_lo=0.4 측 훨씬 초과 — 우회 의도
        명시. ADR-0013 D4 c_lo=0.4 정합 측 검증."""
        c_lo = 0.4
        assert _ADVERSARIAL_CONFIDENCE > c_lo

    def test_inflation_value_below_max(self) -> None:
        """_ADVERSARIAL_CONFIDENCE 측 1.0 max 사용 안 함 — *현실적 LLM 출력*
        sounding (1.0 측 의심 신호 가능)."""
        assert _ADVERSARIAL_CONFIDENCE < 1.0

    def test_move_input_inflated(self) -> None:
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_MOVE))
        assert r.confidence_raw == _ADVERSARIAL_CONFIDENCE

    def test_fallback_input_inflated(self) -> None:
        """base 측 ASK_USER (c_raw=0.0) 측 adversarial 측 inflate — 우회 본질."""
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_FALLBACK))
        assert r.confidence_raw == _ADVERSARIAL_CONFIDENCE

    def test_all_inputs_same_confidence(self) -> None:
        """모든 입력 측 동일 _ADVERSARIAL_CONFIDENCE — base 측 무관 override."""
        w = AdversarialWrapper()
        confidences = {
            w.process(_input(u)).confidence_raw
            for u in (
                _UTTERANCE_MOVE,
                _UTTERANCE_INSPECT,
                _UTTERANCE_RETURN,
                _UTTERANCE_LAND,
                _UTTERANCE_FALLBACK,
            )
        }
        assert confidences == {_ADVERSARIAL_CONFIDENCE}


# -------------------------------------------------------------------- signals 왜곡


class TestSignalsDistortion:
    """signals 측 *확신 sounding* — LLM-측 신호(s2 ρ 높음 + s3 ℓ 0 가까움)만
    변조. s1(접지 엔트로피)은 OVD 전용이라 adversarial(LLM 측 손상=T1)이 손대지
    않음 (§2.1 T1 = OVD 정직). 즉 LLM 이 거짓 확신해도 s1(OVD)이 부재/정직하면
    c = s1·s2·s3 가 오르지 않는다 → 우리 안전 layer sanity 검증."""

    def test_s1_not_emitted_ovd_only(self) -> None:
        # s1 은 OVD 노드 전용 — adversarial(LLM 측 fault)은 signals 에 s1 미포함.
        w = AdversarialWrapper()
        r = w.process(_input())
        assert SIGNAL_ENTROPY not in r.signals

    def test_self_consistency_high(self) -> None:
        w = AdversarialWrapper()
        r = w.process(_input())
        assert r.signals[SIGNAL_SELF_CONSISTENCY] == _ADVERSARIAL_RHO
        assert _ADVERSARIAL_RHO > 0.95  # 매우 일관 sounding 정합

    def test_logprob_near_zero(self) -> None:
        w = AdversarialWrapper()
        r = w.process(_input())
        assert r.signals[SIGNAL_LOGPROB] == _ADVERSARIAL_LOGPROB
        assert _ADVERSARIAL_LOGPROB > -0.5  # 확률 거의 1 sounding 정합

    def test_llm_signals_populated_s1_absent(self) -> None:
        """LLM-측 신호(s2·s3)는 채움(확신 sounding) + s1 부재 — None 측 estimator
        측 fallback 회피. s1 은 OVD 전용이라 키 자체 부재."""
        w = AdversarialWrapper()
        r = w.process(_input())
        assert SIGNAL_ENTROPY not in r.signals
        assert r.signals[SIGNAL_SELF_CONSISTENCY] is not None
        assert r.signals[SIGNAL_LOGPROB] is not None

    def test_signals_constant_across_inputs(self) -> None:
        """모든 입력 측 동일 signals — base 측 무관 override 정합."""
        w = AdversarialWrapper()
        signal_tuples = {
            (
                w.process(_input(u)).signals[SIGNAL_SELF_CONSISTENCY],
                w.process(_input(u)).signals[SIGNAL_LOGPROB],
            )
            for u in (_UTTERANCE_MOVE, _UTTERANCE_RETURN, _UTTERANCE_FALLBACK)
        }
        assert len(signal_tuples) == 1


# -------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_same_input_same_result(self) -> None:
        w = AdversarialWrapper()
        r1 = w.process(_input())
        r2 = w.process(_input())
        assert r1 == r2

    def test_two_instances_same_result(self) -> None:
        """별 인스턴스 측 동일 입력 측 동일 결과 — wrapper 측 stateless 정합."""
        w1 = AdversarialWrapper()
        w2 = AdversarialWrapper()
        r1 = w1.process(_input())
        r2 = w2.process(_input())
        assert r1 == r2


# -------------------------------------------------------------------- args 보존


class TestArgsPreservation:
    """base 측 typed_action.args 측 보존 — Tier 2 schema 검증 측 별 layer 측
    *args 측 새 skill 측 부적합* 측 차단 자리 (ADR-0013 D3).

    예: base 측 move_to args 측 adversarial 측 inspect skill 측 swap 후 args
    측 *부적합* (inspect 측 target_id/viewpoint 필요). 본 mock 측 *args 측
    그대로* 측 Tier 2 측 차단 직접 검증 자리.
    """

    def test_args_passed_through_from_base(self) -> None:
        """base 측 classifier 측 args = {} 측 보존 (classifier stub 측 args 측
        placeholder)."""
        w = AdversarialWrapper()
        r = w.process(_input(_UTTERANCE_MOVE))
        # classifier 측 args 측 {} 측 placeholder — adversarial 측 보존.
        assert r.typed_action.args == {}


# -------------------------------------------------------------------- distinct from other wrappers


class TestDistinctFromOtherWrappers:
    """AdversarialWrapper 측 다른 wrapper (Cloud/Edge/VLA/Classifier) 측
    distinct signature — paper §C ablation 측 각 wrapper distinct readout 정합."""

    def test_adversarial_distinct_from_wrapped(self) -> None:
        """adversarial 측 wrap 대상 (CloudLLMWrapper gpt-4o) 측 *반드시* distinct
        — 왜곡 layer 측 본질."""
        adv = AdversarialWrapper()
        wrapped = CloudLLMWrapper(identifier='gpt-4o')
        r_adv = adv.process(_input(_UTTERANCE_MOVE))
        r_wrapped = wrapped.process(_input(_UTTERANCE_MOVE))

        # skill 측 swap (MOVE_TO → INSPECT) 측 typed_action distinct.
        assert r_adv.typed_action != r_wrapped.typed_action
        # confidence 측 inflation 측 distinct (base 측 0.95 측 매치 가능성 거의 0).
        assert r_adv.confidence_raw != r_wrapped.confidence_raw

    def test_adversarial_distinct_signals_from_wrapped(self) -> None:
        """signals 측 왜곡 — wrap 대상 측 mock signals 측 distinct."""
        adv = AdversarialWrapper()
        wrapped = CloudLLMWrapper(identifier='gpt-4o')
        r_adv = adv.process(_input())
        r_wrapped = wrapped.process(_input())

        # LLM-측 신호(s2·s3) distinct (확신 sounding). s1 은 양쪽 모두 부재
        # (OVD 전용) — 비교 대상 아님.
        assert SIGNAL_ENTROPY not in r_adv.signals
        assert SIGNAL_ENTROPY not in r_wrapped.signals
        assert (
            r_adv.signals[SIGNAL_SELF_CONSISTENCY]
            != r_wrapped.signals[SIGNAL_SELF_CONSISTENCY]
        )
        assert r_adv.signals[SIGNAL_LOGPROB] != r_wrapped.signals[SIGNAL_LOGPROB]

    def test_adversarial_signals_distinct_from_vla(self) -> None:
        """Adversarial signals 측 VLA wrapper 측 *반드시 distinct* (상수 패턴 확인).

        C14 이후 8-distinct-signatures test (SHA-256 mock 의존) 제거 대체 —
        adversarial 측 고정 signals (ρ 0.99 / ℓ -0.05) 측 VLA 측 mock signals
        측 구간 밖 확인. s1 은 양쪽 모두 OVD 전용이라 부재 — s2 로 비교.
        """
        from intent_llm.vla import IDENTIFIER as VLA_ID
        from intent_llm.vla import VLAWrapper, VISION_KEY

        vision_input = IntentInput(
            utterance=_UTTERANCE_MOVE,
            scenario_id='S5',
            context_graph={VISION_KEY: '/path/to/frame.png'},
        )

        adv = AdversarialWrapper()
        vla = VLAWrapper(identifier=VLA_ID)

        r_adv = adv.process(vision_input)
        r_vla = vla.process(vision_input)

        # adversarial 측 signals 고정값 측 VLA mock signals 측 다름 (구간 외).
        # s1 은 양쪽 부재(OVD 전용) → s2(self-consistency)로 비교.
        assert SIGNAL_ENTROPY not in r_adv.signals
        assert SIGNAL_ENTROPY not in r_vla.signals
        assert (
            r_adv.signals[SIGNAL_SELF_CONSISTENCY]
            != r_vla.signals[SIGNAL_SELF_CONSISTENCY]
        )


# -------------------------------------------------------------------- IntentResult contract


class TestIntentResultContract:
    def test_returns_intent_result(self) -> None:
        w = AdversarialWrapper()
        r = w.process(_input())
        assert isinstance(r, IntentResult)

    def test_confidence_raw_in_valid_range(self) -> None:
        w = AdversarialWrapper()
        r = w.process(_input())
        assert 0.0 <= r.confidence_raw <= 1.0

    def test_skill_in_catalog(self) -> None:
        """adversarial 측 출력 skill 측 *반드시* 카탈로그 5 skill 내 — closed
        vocabulary 정합."""
        w = AdversarialWrapper()
        for u in (_UTTERANCE_MOVE, _UTTERANCE_RETURN, _UTTERANCE_LAND, _UTTERANCE_FALLBACK):
            r = w.process(_input(u))
            assert r.typed_action.skill in ALL_SKILLS
