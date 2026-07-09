"""의도해석기 wrapper 식별자 → 인스턴스 매핑.

[ADR-0018 D3](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d3)
+ [ADR-0014](../../../docs/handover/decisions/0014-llm-backbone-six-lock.md) 측 9
식별자 (Cloud 3 + Edge 3 + VLA 1 + Classifier 1 + Adversarial 1) — runner.py 측
trial composition 측 wrapper instance 측 *식별자 기반 lookup* 입구.

본 PR (B7 #12 분할 2b-4) 측 등록 wrapper 9 종 — ADR-0018 D3 5-way ablation
*완전 cover*:
  - `closed-vocabulary-keyword` → ClassifierWrapper (실 stub, 분할 2b-1)
  - `gpt-4o` · `gpt-5` · `gpt-5.5` → CloudLLMWrapper (mock, 분할 2b-2)
  - `llama-3.2-11b-vision` · `qwen2.5-vl-7b` · `gemma-4-e4b` → EdgeLLMWrapper
    (mock, 분할 2b-2)
  - `openvla-7b` → VLAWrapper (mock, 분할 2b-3) — ADR-0018 D3 row 3 + §A3
  - `gpt-4o-injected` → AdversarialWrapper (mock, 분할 2b-4) — ADR-0018 D3
    row 5 + D5 기본 wrap 대상 = GPT-4o

## 호출 패턴

    wrapper = get_wrapper('gpt-4o')
    result = wrapper.process(IntentInput(utterance='...', scenario_id='S5'))

runner.py 측 BaselineConfig + scenario 측 wrapper 식별자 선택 (별 layer — 본
PR scope 밖).
"""

from __future__ import annotations

from typing import Dict, Tuple

from intent_llm.backbones import (
    CLOUD_BACKBONES,
    EDGE_BACKBONES,
)
from intent_llm.classifier import IDENTIFIER as CLASSIFIER_ID
from intent_llm.classifier import ClassifierWrapper
from intent_llm.cloud_llm import CloudLLMWrapper
from intent_llm.edge_llm import EdgeLLMWrapper
from intent_llm.interface import IntentWrapper
from intent_llm.adversarial import IDENTIFIER as ADVERSARIAL_ID
from intent_llm.adversarial import AdversarialWrapper
from intent_llm.vla import IDENTIFIER as VLA_ID
from intent_llm.vla import VLAWrapper


def _build_default_registry() -> Dict[str, IntentWrapper]:
    """1차 등록 — ClassifierWrapper (분할 2b-1) + Cloud/Edge 6 wrapper (분할
    2b-2) + VLAWrapper (분할 2b-3) + AdversarialWrapper (분할 2b-4)."""
    registry: Dict[str, IntentWrapper] = {
        CLASSIFIER_ID: ClassifierWrapper(),
    }
    for spec in CLOUD_BACKBONES:
        registry[spec.identifier] = CloudLLMWrapper(identifier=spec.identifier)
    for spec in EDGE_BACKBONES:
        registry[spec.identifier] = EdgeLLMWrapper(identifier=spec.identifier)
    registry[VLA_ID] = VLAWrapper(identifier=VLA_ID)
    registry[ADVERSARIAL_ID] = AdversarialWrapper(identifier=ADVERSARIAL_ID)
    return registry


_REGISTRY: Dict[str, IntentWrapper] = _build_default_registry()


def get_wrapper(identifier: str) -> IntentWrapper:
    """식별자 → wrapper instance.

    Args:
        identifier: ADR-0018 D3 + ADR-0014 측 9 식별자 중 하나. 현재 PR 측
            classifier 1 + cloud 3 + edge 3 + vla 1 + adversarial 1 = 9 식별자
            등록 (5-way ablation 완전 cover).

    Returns:
        IntentWrapper Protocol 충족 instance.

    Raises:
        KeyError: 등록 외 식별자. error message 측 등록된 식별자 list 명시.
    """
    if identifier not in _REGISTRY:
        registered = sorted(_REGISTRY.keys())
        raise KeyError(
            f'identifier={identifier!r} 미등록 — 현재 등록: {registered}. '
            f'추가 wrapper 측 본 모듈 측 _build_default_registry() 측 신설.'
        )
    return _REGISTRY[identifier]


def list_registered() -> Tuple[str, ...]:
    """현재 등록된 식별자 sorted tuple — test + runner.py 측 enumeration."""
    return tuple(sorted(_REGISTRY.keys()))
