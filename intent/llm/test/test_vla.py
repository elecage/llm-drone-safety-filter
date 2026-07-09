"""intent_llm.vla 단위 테스트.

VLAWrapper 측 mock 동작 + vision presence boost + ASK_USER fallback 보존 +
Protocol 충족 + determinism + LLM mock 측 distinct signature.

B7 #12 분할 2b-3 scope — ADR-0018 D3 row 3 + §A3 단일 식별자 (OpenVLA-7B).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from intent_llm._llm_mock import (
    _MOCK_LOGPROB_MAX,
    _MOCK_LOGPROB_MIN,
    _MOCK_RHO_MAX,
    _MOCK_RHO_MIN,
)
from intent_llm.cloud_llm import CloudLLMWrapper
from intent_llm.edge_llm import EdgeLLMWrapper
from intent_llm.interface import (
    SIGNAL_ENTROPY,
    SIGNAL_LOGPROB,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    IntentWrapper,
)
from intent_llm.skill_catalog import SkillName
from intent_llm.vla import (
    CATEGORY,
    IDENTIFIER,
    VISION_KEY,
    _VISION_CONFIDENCE_BOOST_MAGNITUDE,
    VLAWrapper,
    _has_vision,
)


# C14 이후 CloudLLMWrapper.process() 측 실 API 호출 시도. 비교 테스트 측 패치 필요.
@pytest.fixture(autouse=True)
def _patch_cloud_llm_to_classifier():
    """CloudLLMWrapper.process 측 ClassifierWrapper 위임 — API 없이 테스트 가능."""
    from intent_llm.classifier import ClassifierWrapper
    from intent_llm.cloud_llm import CloudLLMWrapper

    classifier = ClassifierWrapper()
    with patch.object(CloudLLMWrapper, 'process', lambda self, inp: classifier.process(inp)):
        yield


# 발화 — MOVE_TO trigger (classifier 측 'go' substring 매치 측 base != 0).
_UTTERANCE_GO = 'Please go forward'
# 매치 0 → ASK_USER fallback.
_UTTERANCE_FALLBACK = 'asdfgh xyzqw'

# Vision frame mock — 실 RGB array 대신 *truthy* placeholder. _has_vision 측
# None 만 reject — 다른 truthy value 측 vision present 측 처리.
_VISION_PLACEHOLDER = '/path/to/frame.png'
_VISION_ARRAY_LIKE = [[0, 0, 0]]  # numpy 의존 회피 — list 측 truthy.


def _input_no_vision(utterance: str = _UTTERANCE_GO) -> IntentInput:
    return IntentInput(utterance=utterance, scenario_id='S5')


def _input_with_vision(
    utterance: str = _UTTERANCE_GO,
    frame: object = _VISION_PLACEHOLDER,
) -> IntentInput:
    return IntentInput(
        utterance=utterance,
        scenario_id='S5',
        context_graph={VISION_KEY: frame},
    )


# -------------------------------------------------------------------- categories


class TestCategoriesAndIdentifier:
    def test_category(self) -> None:
        assert CATEGORY == 'vla'
        assert VLAWrapper.category == CATEGORY

    def test_identifier_locked(self) -> None:
        """ADR-0018 D3 row 3 + §A3 — OpenVLA-7B *단일 식별자*."""
        assert IDENTIFIER == 'openvla-7b'

    def test_vision_key_locked(self) -> None:
        """VISION_KEY 측 single source-of-truth — 변경 시 본 test 측 명시적
        잠금 깨짐 → 후속 wrapper · estimator · runner 측 영향 명시 의무."""
        assert VISION_KEY == 'camera_frame'


# -------------------------------------------------------------------- construction


class TestConstruction:
    def test_constructs_with_default_identifier(self) -> None:
        w = VLAWrapper(identifier=IDENTIFIER)
        assert w.identifier == IDENTIFIER
        assert w.category == 'vla'

    def test_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            VLAWrapper(identifier='')

    def test_whitespace_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            VLAWrapper(identifier='   ')


# -------------------------------------------------------------------- protocol


class TestProtocol:
    def test_satisfies_intent_wrapper(self) -> None:
        assert isinstance(VLAWrapper(identifier=IDENTIFIER), IntentWrapper)


# -------------------------------------------------------------------- has_vision


class TestHasVisionHelper:
    """_has_vision 측 None-safe + key-present + value-present 측 3 차원 검증."""

    def test_no_context_graph_false(self) -> None:
        inp = IntentInput(utterance='go', scenario_id='S5')
        assert _has_vision(inp) is False

    def test_empty_context_graph_false(self) -> None:
        inp = IntentInput(utterance='go', scenario_id='S5', context_graph={})
        assert _has_vision(inp) is False

    def test_missing_vision_key_false(self) -> None:
        inp = IntentInput(
            utterance='go', scenario_id='S5', context_graph={'other_key': 'x'}
        )
        assert _has_vision(inp) is False

    def test_vision_key_with_none_value_false(self) -> None:
        """value=None 측 placeholder missing 측 회피 — silent vision-absent 차단."""
        inp = IntentInput(
            utterance='go', scenario_id='S5', context_graph={VISION_KEY: None}
        )
        assert _has_vision(inp) is False

    def test_vision_key_with_string_path_true(self) -> None:
        assert _has_vision(_input_with_vision(frame=_VISION_PLACEHOLDER)) is True

    def test_vision_key_with_array_like_true(self) -> None:
        assert _has_vision(_input_with_vision(frame=_VISION_ARRAY_LIKE)) is True


# -------------------------------------------------------------------- ASK_USER fallback


class TestAskUserFallbackPreserved:
    """ASK_USER fallback (c_raw=0.0) 측 vision boost 적용 안 함 — Tier 2 c_lo
    trigger 측 유지. PR #125 C-1 safety-first design 정합 패턴."""

    def test_fallback_no_vision(self) -> None:
        w = VLAWrapper(identifier=IDENTIFIER)
        r = w.process(_input_no_vision(_UTTERANCE_FALLBACK))
        assert r.typed_action.skill == SkillName.ASK_USER
        assert r.confidence_raw == 0.0

    def test_fallback_with_vision(self) -> None:
        """Vision present 측에도 ASK_USER fallback 측 유지 — vision 측 utterance
        측 모호함 측 *덮지 않음* (mock 한계)."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r = w.process(_input_with_vision(_UTTERANCE_FALLBACK))
        assert r.typed_action.skill == SkillName.ASK_USER
        assert r.confidence_raw == 0.0


# -------------------------------------------------------------------- vision boost


class TestVisionBoost:
    """Vision present 측 base mock 측 *추가* deterministic boost 측 적용."""

    def test_no_vision_equals_base_llm_mock(self) -> None:
        """Vision 부재 측 VLA mock 측 base LLM mock 측 *동일* 결과 — VLA 측
        vision 측 핵심 입력 부재 측 degraded 가정 정합. _LLMMockBase.process()
        측 동일 호출 시 동일 결과 보장 (super delegation)."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r_vla = w.process(_input_no_vision())

        # base mock 측 동일 identifier 측 동일 결과 — _LLMMockBase 측 결정론.
        # 별 wrapper class 측 동일 base 결과 검증 측 *직접* 같은 결과 가져옴.
        from intent_llm._llm_mock import _LLMMockBase

        class _ProbeWrapper(_LLMMockBase):
            # M-3 정정 (2026-05-27) — 이전 category='vla' 측 *VLA 가 아닌 base
            # mock 검증용* 측 misleading → 'probe' 측 정확. _LLMMockBase.__init__
            # 측 non-empty string 외 제약 없음.
            category: str = 'probe'

        probe = _ProbeWrapper(identifier=IDENTIFIER)
        r_base = probe.process(_input_no_vision())

        assert r_vla.confidence_raw == r_base.confidence_raw
        assert r_vla.typed_action == r_base.typed_action
        # s1 은 양쪽 모두 부재 (OVD 전용, §2.1) — 비교 대상 아님.
        assert SIGNAL_ENTROPY not in r_vla.signals
        assert SIGNAL_ENTROPY not in r_base.signals

    def test_vision_present_differs_from_no_vision(self) -> None:
        """동일 utterance + vision present vs absent 측 confidence 차이 — vision
        channel 측 *별 차원* mock variation 측 입증."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r_no = w.process(_input_no_vision())
        r_with = w.process(_input_with_vision())

        # ASK_USER fallback 측 아닌 base — boost 적용 자리.
        assert r_no.confidence_raw > 0.0
        # vision boost 측 ±_VISION_CONFIDENCE_BOOST_MAGNITUDE → confidence 차이
        # 측 *대부분 nonzero* (u_vision=0.5 측 정확히 boost=0 측 edge case).
        # SHA-256 측 u_vision 측 정확히 0.5 일 가능성 측 무시 가능.
        assert r_no.confidence_raw != r_with.confidence_raw

    def test_vision_boost_magnitude_bounded(self) -> None:
        """Vision boost 측 magnitude 측 ±_VISION_CONFIDENCE_BOOST_MAGNITUDE 내."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r_no = w.process(_input_no_vision())
        r_with = w.process(_input_with_vision())

        diff = abs(r_with.confidence_raw - r_no.confidence_raw)
        # tolerance — boost magnitude + float error + clip side-effect.
        assert diff <= _VISION_CONFIDENCE_BOOST_MAGNITUDE + 1e-6

    def test_vision_present_preserves_skill(self) -> None:
        """Vision boost 측 confidence 만 변경 — typed_action 측 base 측 동일."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r_no = w.process(_input_no_vision())
        r_with = w.process(_input_with_vision())
        assert r_no.typed_action == r_with.typed_action

    def test_vision_present_preserves_signals(self) -> None:
        """Vision boost 측 signals 측 변경 없음 — mock 한계 (실 VLA 측 logprob
        측 vision 영향 가능, 본 mock 측 모델링 X)."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r_no = w.process(_input_no_vision())
        r_with = w.process(_input_with_vision())
        # s1 은 부재 (OVD 전용, §2.1).
        assert SIGNAL_ENTROPY not in r_no.signals
        assert (
            r_no.signals[SIGNAL_SELF_CONSISTENCY]
            == r_with.signals[SIGNAL_SELF_CONSISTENCY]
        )
        assert r_no.signals[SIGNAL_LOGPROB] == r_with.signals[SIGNAL_LOGPROB]


# -------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_same_input_same_result(self) -> None:
        w = VLAWrapper(identifier=IDENTIFIER)
        r1 = w.process(_input_with_vision())
        r2 = w.process(_input_with_vision())
        assert r1 == r2

    def test_vision_content_does_not_affect_mock(self) -> None:
        """Mock 측 vision *content* 측 사용 안 함 — *presence* 만. 다른 frame
        측 동일 결과. 실 VLA wrapper 측 후속 PR 측 본 invariant 측 깨짐 (frame
        content 측 결과 영향)."""
        w = VLAWrapper(identifier=IDENTIFIER)
        r1 = w.process(_input_with_vision(frame=_VISION_PLACEHOLDER))
        r2 = w.process(_input_with_vision(frame=_VISION_ARRAY_LIKE))
        assert r1.confidence_raw == r2.confidence_raw
        assert r1.typed_action == r2.typed_action


# -------------------------------------------------------------------- distinct from LLM mocks


class TestDistinctFromLLMMocks:
    """VLA mock 측 6 LLM mock 측 *distinct signature* — paper §C ablation 측
    8 wrapper distinct 결과 보장. vision channel 측 별 hash payload 측 보장."""

    def test_vla_distinct_from_cloud_with_vision(self) -> None:
        """동일 utterance + vision present 측 VLA vs Cloud mock 측 signature
        distinct. base classifier 측 동일 skill 측 confidence 측 다름 (vision
        boost + 다른 identifier hash)."""
        vla = VLAWrapper(identifier=IDENTIFIER)
        cloud = CloudLLMWrapper(identifier='gpt-4o')
        r_vla = vla.process(_input_with_vision())
        r_cloud = cloud.process(_input_with_vision())

        sig_vla = (
            r_vla.confidence_raw,
            r_vla.signals[SIGNAL_SELF_CONSISTENCY],
            r_vla.signals[SIGNAL_LOGPROB],
        )
        sig_cloud = (
            r_cloud.confidence_raw,
            r_cloud.signals[SIGNAL_SELF_CONSISTENCY],
            r_cloud.signals[SIGNAL_LOGPROB],
        )
        assert sig_vla != sig_cloud

    def test_vla_distinct_from_edge_with_vision(self) -> None:
        vla = VLAWrapper(identifier=IDENTIFIER)
        edge = EdgeLLMWrapper(identifier='gemma-4-e4b')
        r_vla = vla.process(_input_with_vision())
        r_edge = edge.process(_input_with_vision())

        sig_vla = (
            r_vla.confidence_raw,
            r_vla.signals[SIGNAL_SELF_CONSISTENCY],
            r_vla.signals[SIGNAL_LOGPROB],
        )
        sig_edge = (
            r_edge.confidence_raw,
            r_edge.signals[SIGNAL_SELF_CONSISTENCY],
            r_edge.signals[SIGNAL_LOGPROB],
        )
        assert sig_vla != sig_edge

    def test_vla_rho_logprob_populated_unlike_classifier(self) -> None:
        """VLA mock 측 ρ/ℓ 채움 — classifier 측 ρ=None/ℓ=None 측 대비.

        C14 이후 7-distinct-signatures test (SHA-256 mock 의존) 제거 대체 —
        VLA 측 *M회 추론 신호* (ρ/ℓ) 측 채움 여부 검증.
        """
        from intent_llm.classifier import ClassifierWrapper

        vla = VLAWrapper(identifier=IDENTIFIER)
        clf = ClassifierWrapper()
        r_vla = vla.process(_input_with_vision())
        r_clf = clf.process(_input_with_vision())

        # VLA: ρ/ℓ 채움. Classifier: ρ=None, ℓ=None.
        assert r_vla.signals[SIGNAL_SELF_CONSISTENCY] is not None
        assert r_vla.signals[SIGNAL_LOGPROB] is not None
        assert r_clf.signals[SIGNAL_SELF_CONSISTENCY] is None
        assert r_clf.signals[SIGNAL_LOGPROB] is None


# -------------------------------------------------------------------- mock signal ranges


class TestSignalsPopulated:
    """VLA mock 측 ρ/ℓ 측 base LLM mock 측 동일 채움 — classifier 측 None 측 대비."""

    def test_rho_in_mock_range(self) -> None:
        w = VLAWrapper(identifier=IDENTIFIER)
        r = w.process(_input_with_vision())
        rho = r.signals[SIGNAL_SELF_CONSISTENCY]
        assert rho is not None
        assert _MOCK_RHO_MIN <= rho <= _MOCK_RHO_MAX

    def test_logprob_in_mock_range(self) -> None:
        w = VLAWrapper(identifier=IDENTIFIER)
        r = w.process(_input_with_vision())
        logprob = r.signals[SIGNAL_LOGPROB]
        assert logprob is not None
        assert _MOCK_LOGPROB_MIN <= logprob <= _MOCK_LOGPROB_MAX

    def test_returns_intent_result(self) -> None:
        w = VLAWrapper(identifier=IDENTIFIER)
        r = w.process(_input_with_vision())
        assert isinstance(r, IntentResult)
