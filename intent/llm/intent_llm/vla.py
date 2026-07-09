"""VLA wrapper — OpenVLA-7B (Kim et al., arXiv:2406.09246) mock.

[ADR-0018 D3](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d3)
표 row 3 — *7B VLA* 카테고리. 1차 논문 측 *최소 조합으로 의도해석기-불가지 입증*
원칙 정합 측 UAV-VLA 측 paper-2 이월 ([ADR-0018 §Alternatives considered (A3)](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#alternatives-considered))
— 본 카테고리 측 OpenVLA-7B *단일 식별자*.

출력 contract = LLM wrapper 측 동일 (TypedAction + confidence_raw + ρ/ℓ signals)
— [intent_layer_theory §3.1](../../../docs/research_notes/intent_layer_theory.md)
인터페이스 IF 정합. ask 액션 측 외부 노출 X ([ADR-0018 D3](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d3)
부류 Y 정합 — VLA 측 ask_user 직접 산출 X, 단 우리 mock 측 classifier delegate
fallback 측 ASK_USER 산출 가능 — paper §C ablation 측 *intent-agnostic safety*
narrative 측 정합 — Tier 2 c_lo 게이트 측 정상 처리).

## Mock contract — 본 PR (B7 #12 분할 2b-3) scope

실 OpenVLA-7B 추론 *없이* IntentResult 산출. 후속 PR 측 process() 측 *swap*:
  - HuggingFace `openvla/openvla-7b` 또는 공식 release weight pin.
  - 추론 backend — llama.cpp 측 VLM 지원 또는 transformers Direct (ADR-0014 D2
    "Apple MLX 또는 llama.cpp Metal" 정합 측 *VLM-호환 backend* 측 미확정 —
    paper §C 본실험 직전 결정).
  - Vision preprocessing — RGB H×W×3, 224×224 또는 모델 native resolution.
  - Action decoding — OpenVLA token-level action prediction → ADR-0013 D2 5
    스킬 mapping (별 PR 측 mapping layer 잠금).

## Vision input contract — context_graph dict 측 key 추가

본 PR 측 [NEXT_SESSION.md](../../../docs/handover/NEXT_SESSION.md) 측 설계 결정
"옵션 1: context_graph dict 측 추가" 정합 — `IntentInput.context_graph[VISION_KEY]`
측 vision frame 측 전달:

  - VALUE type: numpy array (RGB H×W×3) 또는 file path string (mock 측 *content*
    측 사용 안 함, *presence* 측 binary flag 만 활용 — 실 VLA wrapper 측 후속
    PR 측 사용).
  - VISION_KEY 측 `vla.VISION_KEY` 측 single source-of-truth 측 export.
  - Vision *부재* (context_graph is None 또는 VISION_KEY missing) 측 base LLM
    mock 동작 — *VLA 측 vision 측 핵심 입력 부재 측 degraded* 가정 (boost 적용
    안 함).
  - Vision *존재* 측 추가 ±_VISION_CONFIDENCE_BOOST_MAGNITUDE deterministic
    variation — backbone-dependent + vision-presence-dependent 측 *별 차원
    mock variation* (실 VLA 측 vision 측 활용 측 confidence 변동 가정).

## Interface 변경 회피

본 설계 측 IntentInput dataclass 측 *변경 없음* — context_graph dict schema
측 *typed validation* 측 어려움 trade-off. 후속 PR 측 typed schema 잠금 가치
([NEXT_SESSION.md](../../../docs/handover/NEXT_SESSION.md) 측 §"4. vision input
design" 정합).
"""

from __future__ import annotations

from typing import Mapping, Optional

from intent_llm._llm_mock import (
    _LLMMockBase,
    _hash_to_unit_interval,
)
from intent_llm.interface import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    IntentInput,
    IntentResult,
)


# ADR-0018 D3 카테고리 식별자.
CATEGORY: str = 'vla'

# 본 카테고리 단일 식별자 — ADR-0018 D3 row 3 + §A3 (UAV-VLA 측 paper-2 이월).
IDENTIFIER: str = 'openvla-7b'

# Vision frame 측 context_graph dict key — 본 모듈 측 single source-of-truth.
# 후속 wrapper · estimator · runner 측 본 상수 참조. typo 측 silent vision-absent
# 측 회피.
VISION_KEY: str = 'camera_frame'

# Vision-augmented boost magnitude — _LLMMockBase 측 ±0.1 base boost 위 추가
# ±0.05 variation. 실 VLA 측 vision 측 활용 측 confidence 변동 가정 — base LLM
# 측 *별 차원* 측 noise 측 시뮬레이션. paper §C ablation 측 6 LLM 백본 + VLA
# 측 *distinct signature* 보장 (vision channel 별 hash payload).
_VISION_CONFIDENCE_BOOST_MAGNITUDE: float = 0.05


def _has_vision(intent_input: IntentInput) -> bool:
    """context_graph 측 VISION_KEY 측 존재 여부 — None-safe.

    Args:
        intent_input: IntentInput.

    Returns:
        True 측 context_graph 측 dict + VISION_KEY 측 존재. value 측 None 아닌
        경우 만 True (실 vision frame 측 missing 측 placeholder None 측 회피).
    """
    if intent_input.context_graph is None:
        return False
    if VISION_KEY not in intent_input.context_graph:
        return False
    return intent_input.context_graph[VISION_KEY] is not None


class VLAWrapper(_LLMMockBase):
    """OpenVLA-7B wrapper — IntentWrapper Protocol 충족 (mock).

    호출 패턴:
        wrapper = VLAWrapper(identifier='openvla-7b')
        result = wrapper.process(
            IntentInput(
                utterance='...',
                scenario_id='S5',
                context_graph={vla.VISION_KEY: rgb_array},  # 또는 path
            )
        )

    Mock 측 동작:
      1. `_LLMMockBase.process()` 측 호출 — base classifier delegate + backbone
         SHA-256 측 ±0.1 boost + ρ/ℓ mock + ASK_USER fallback 보존.
      2. ASK_USER fallback (c_raw=0.0) 측 *vision boost 적용 안 함* — Tier 2
         c_lo trigger 측 유지 (PR #125 C-1 정합 패턴).
      3. Vision present 측 (identifier, utterance, scenario_id, 'vision') 측
         SHA-256 측 추가 ±0.05 deterministic boost. clip 측 [0, 1].
      4. Vision absent 측 base 결과 측 그대로 return — VLA 측 vision 측 핵심
         입력 부재 측 degraded (LLM mock 측 동일 동작).
    """

    category: str = CATEGORY

    def process(self, intent_input: IntentInput) -> IntentResult:
        """utterance + (optional) vision → mock IntentResult (deterministic).

        Args:
            intent_input: IntentInput. `context_graph[VISION_KEY]` 측 RGB array
                또는 path. 본 mock 측 *content* 측 사용 X, *presence* 측만
                vision-augmented boost flag 활용.

        Returns:
            IntentResult — base LLM mock 측 결과 측 vision boost 적용 (presence
            측 적용 안 함).
        """
        base = super().process(intent_input)

        # ASK_USER fallback (c_raw=0.0) 측 boost 적용 안 함 — Tier 2 c_lo
        # trigger 측 유지 (PR #125 C-1 safety-first design 정합).
        if base.confidence_raw == CONFIDENCE_MIN:
            return base

        # Vision absent 측 base 그대로 — VLA 측 vision 측 핵심 입력 부재 측
        # degraded (LLM mock 측 동일 동작). 실 VLA 측 vision 없으면 *추론 불가*
        # 측 wrapper 측 *결과 산출 가능* contract 정합 — Protocol docstring 측
        # "어떠한 입력 측 항상 유효 IntentResult 산출" 정합.
        if not _has_vision(intent_input):
            return base

        # Vision present — 추가 deterministic boost. base mock 측 ±0.1 위
        # 별 차원 ±0.05 — vision channel 측 distinct hash payload (suffix
        # '\x00vision') 측 LLM mock 측 collision 회피. 6 LLM + VLA 측 distinct
        # signature 보장 (paper §C ablation 정합).
        payload = (
            f'{self.identifier}\x00{intent_input.utterance}'
            f'\x00{intent_input.scenario_id}\x00vision'
        )
        u_vision = _hash_to_unit_interval(payload)
        vision_boost = (u_vision - 0.5) * 2.0 * _VISION_CONFIDENCE_BOOST_MAGNITUDE
        boosted = base.confidence_raw + vision_boost
        confidence_raw = max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, boosted))

        # signals 측 변경 없음 — base mock 측 ρ/ℓ 측 vision 측 영향 모델링 안 함
        # (mock 한계 — 실 VLA 측 vision 측 token logprob 측 영향 가능). 후속
        # PR 측 실 VLA inference 측 ρ/ℓ 측 직접 산출.
        signals: Mapping[str, Optional[float]] = base.signals

        return IntentResult(
            typed_action=base.typed_action,
            confidence_raw=confidence_raw,
            signals=signals,
        )
