"""intent_llm.registry 단위 테스트.

식별자 → wrapper instance 매핑 + 미등록 측 KeyError + list_registered 검증.
B7 #12 분할 2b-4 측 9 wrapper 등록 (classifier 1 + cloud 3 + edge 3 + vla 1 +
adversarial 1) — 5-way ablation 완전 cover.
"""

from __future__ import annotations

import pytest

from intent_llm.adversarial import IDENTIFIER as ADVERSARIAL_ID
from intent_llm.adversarial import AdversarialWrapper
from intent_llm.backbones import ALL_BACKBONES, CLOUD_BACKBONES, EDGE_BACKBONES
from intent_llm.classifier import IDENTIFIER as CLASSIFIER_ID
from intent_llm.classifier import ClassifierWrapper
from intent_llm.cloud_llm import CloudLLMWrapper
from intent_llm.edge_llm import EdgeLLMWrapper
from intent_llm.interface import IntentInput, IntentWrapper
from intent_llm.registry import get_wrapper, list_registered
from intent_llm.vla import IDENTIFIER as VLA_ID
from intent_llm.vla import VLAWrapper


class TestGetWrapper:
    def test_classifier_registered(self) -> None:
        wrapper = get_wrapper(CLASSIFIER_ID)
        assert isinstance(wrapper, ClassifierWrapper)

    def test_returned_satisfies_protocol(self) -> None:
        wrapper = get_wrapper(CLASSIFIER_ID)
        assert isinstance(wrapper, IntentWrapper)

    def test_unknown_identifier_raises(self) -> None:
        """5-way ablation 외 식별자 — 후속 PR scope (없음)."""
        with pytest.raises(KeyError, match='미등록'):
            get_wrapper('nonexistent-wrapper')

    def test_unknown_error_lists_registered(self) -> None:
        """error message 측 현재 등록 식별자 명시 — 후속 PR 진입 시 helpful."""
        with pytest.raises(KeyError) as exc:
            get_wrapper('nonexistent')
        assert CLASSIFIER_ID in str(exc.value)


class TestListRegistered:
    def test_returns_tuple(self) -> None:
        registered = list_registered()
        assert isinstance(registered, tuple)

    def test_includes_classifier(self) -> None:
        assert CLASSIFIER_ID in list_registered()

    def test_sorted(self) -> None:
        registered = list_registered()
        assert registered == tuple(sorted(registered))

    def test_size_matches_2b4_scope(self) -> None:
        """B7 #12 분할 2b-4 scope = classifier 1 + cloud 3 + edge 3 + vla 1 +
        adversarial 1 = **9** — 5-way ablation 완전 cover.

        후속 wrapper 확장 시 본 test 측 갱신 — *명시적* scope 잠금.
        """
        assert len(list_registered()) == 9


class TestWrapperUsable:
    def test_can_process_via_registry(self) -> None:
        """registry 측 lookup 측 wrapper 측 process 호출 가능."""
        wrapper = get_wrapper(CLASSIFIER_ID)
        result = wrapper.process(
            IntentInput(utterance='가 줘', scenario_id='S5')
        )
        assert 0.0 <= result.confidence_raw <= 1.0


class TestADR0014Backbones:
    """ADR-0014 D1 6 백본 측 모두 registry 등록 — B7 #12 분할 2b-2 정합."""

    def test_all_cloud_backbones_registered(self) -> None:
        registered = list_registered()
        for spec in CLOUD_BACKBONES:
            assert spec.identifier in registered

    def test_all_edge_backbones_registered(self) -> None:
        registered = list_registered()
        for spec in EDGE_BACKBONES:
            assert spec.identifier in registered

    def test_cloud_backbones_are_cloud_wrapper(self) -> None:
        for spec in CLOUD_BACKBONES:
            w = get_wrapper(spec.identifier)
            assert isinstance(w, CloudLLMWrapper)
            assert w.identifier == spec.identifier
            assert w.category == 'cloud_llm'

    def test_edge_backbones_are_edge_wrapper(self) -> None:
        for spec in EDGE_BACKBONES:
            w = get_wrapper(spec.identifier)
            assert isinstance(w, EdgeLLMWrapper)
            assert w.identifier == spec.identifier
            assert w.category == 'edge_llm'

    def test_six_backbones_plus_classifier_plus_vla_plus_adversarial(self) -> None:
        """6 ADR-0014 백본 + 1 classifier + 1 VLA + 1 adversarial = **9**
        등록 (B7 #12 분할 2b-4 — 5-way ablation 완전 cover)."""
        registered = list_registered()
        assert len(registered) == len(ALL_BACKBONES) + 3


class TestVLARegistration:
    """ADR-0018 D3 row 3 + §A3 — OpenVLA-7B 단일 식별자 등록."""

    def test_vla_registered(self) -> None:
        wrapper = get_wrapper(VLA_ID)
        assert isinstance(wrapper, VLAWrapper)
        assert wrapper.identifier == VLA_ID
        assert wrapper.category == 'vla'

    def test_vla_satisfies_protocol(self) -> None:
        wrapper = get_wrapper(VLA_ID)
        assert isinstance(wrapper, IntentWrapper)

    def test_vla_in_list_registered(self) -> None:
        assert VLA_ID in list_registered()


class TestAdversarialRegistration:
    """ADR-0018 D3 row 5 + D5 — GPT-4o wrap 측 단일 식별자 등록."""

    def test_adversarial_registered(self) -> None:
        wrapper = get_wrapper(ADVERSARIAL_ID)
        assert isinstance(wrapper, AdversarialWrapper)
        assert wrapper.identifier == ADVERSARIAL_ID
        assert wrapper.category == 'adversarial'

    def test_adversarial_satisfies_protocol(self) -> None:
        wrapper = get_wrapper(ADVERSARIAL_ID)
        assert isinstance(wrapper, IntentWrapper)

    def test_adversarial_in_list_registered(self) -> None:
        assert ADVERSARIAL_ID in list_registered()

    def test_adversarial_default_wraps_gpt4o(self) -> None:
        """ADR-0018 D5 — 기본 wrap 대상 = GPT-4o."""
        wrapper = get_wrapper(ADVERSARIAL_ID)
        assert isinstance(wrapper, AdversarialWrapper)
        assert wrapper.wrapped_identifier == 'gpt-4o'
