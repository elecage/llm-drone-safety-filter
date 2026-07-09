"""intent_llm._llm_mock + cloud_llm + edge_llm 카테고리·생성·프로토콜 테스트.

_LLMMockBase base class 검증 + Cloud/Edge subclass 측 category·construction·
protocol 검증. process() 동작 검증은 test_cloud_llm.py / test_edge_llm.py 참조.
"""

from __future__ import annotations

import pytest

from intent_llm._llm_mock import _LLMMockBase
from intent_llm.cloud_llm import CATEGORY as CLOUD_CATEGORY
from intent_llm.cloud_llm import CloudLLMWrapper
from intent_llm.edge_llm import CATEGORY as EDGE_CATEGORY
from intent_llm.edge_llm import EdgeLLMWrapper
from intent_llm.interface import (
    IntentResult,
    IntentWrapper,
)


# -------------------------------------------------------------------- categories


class TestCategories:
    def test_cloud_category(self) -> None:
        assert CLOUD_CATEGORY == 'cloud_llm'
        assert CloudLLMWrapper.category == CLOUD_CATEGORY

    def test_edge_category(self) -> None:
        assert EDGE_CATEGORY == 'edge_llm'
        assert EdgeLLMWrapper.category == EDGE_CATEGORY

    def test_cloud_edge_distinct(self) -> None:
        assert CLOUD_CATEGORY != EDGE_CATEGORY


class TestConstruction:
    def test_cloud_constructs(self) -> None:
        w = CloudLLMWrapper(identifier='gpt-4o')
        assert w.identifier == 'gpt-4o'
        assert w.category == 'cloud_llm'

    def test_edge_constructs(self) -> None:
        w = EdgeLLMWrapper(identifier='gemma-4-e4b')
        assert w.identifier == 'gemma-4-e4b'
        assert w.category == 'edge_llm'

    def test_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            CloudLLMWrapper(identifier='')

    def test_whitespace_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            EdgeLLMWrapper(identifier='   ')

    def test_base_without_category_rejected(self) -> None:
        """_LLMMockBase 측 category 미정의 측 NotImplementedError."""
        with pytest.raises(NotImplementedError, match='category'):
            _LLMMockBase(identifier='test')


# -------------------------------------------------------------------- protocol


class TestProtocol:
    def test_cloud_satisfies_protocol(self) -> None:
        assert isinstance(CloudLLMWrapper(identifier='gpt-4o'), IntentWrapper)

    def test_edge_satisfies_protocol(self) -> None:
        assert isinstance(
            EdgeLLMWrapper(identifier='gemma-4-e4b'), IntentWrapper
        )


# -------------------------------------------------------------------- regression


class TestClassifierUnchanged:
    """ClassifierWrapper 측 C14 이후 동작 변경 없음 — regression.

    Cloud/Edge wrapper 측 실 API wiring (C14) 측 ClassifierWrapper 측 무관 —
    동일 IntentResult 계약 유지 검증.
    """

    def test_classifier_still_returns_intent_result(self) -> None:
        from intent_llm.classifier import ClassifierWrapper
        from intent_llm.interface import IntentInput

        r = ClassifierWrapper().process(IntentInput(utterance='go forward', scenario_id='S5'))
        assert isinstance(r, IntentResult)
