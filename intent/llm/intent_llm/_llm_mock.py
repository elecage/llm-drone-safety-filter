"""LLM mock wrapper base — Cloud / Edge wrapper 측 공통 logic.

[B7 #12 분할 2b-2](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
scope — 실 LLM API call (Cloud) / 실 local inference (Edge) *없이* IntentResult
contract 산출. paper §C 측 *infrastructure swap* 측 검증 자리 — 후속 PR 측 process()
구현 측 swap 측 외부 contract 동일.

## Mock logic

1. **Skill identification**: ClassifierWrapper 측 *delegate* — keyword matching
   측 skill 추출. 실 LLM 측 더 정확하나 (자유 형식 NL parsing) mock 측 fallback.
2. **Confidence boost**: backbone_id + utterance 측 SHA-256 측 deterministic
   variation — 백본 별 *결과 변동성* 시뮬레이션 (실 API 측 자연 발생). base
   classifier confidence 측 ±0.1 범위 측 boost.
3. **ρ (self-consistency)**: deterministic mock — backbone-dependent, 보통
   0.7-0.95 범위 (실 LLM 측 $M$ 회 추론 측 majority vote rate 가정).
4. **ℓ (logprob)**: deterministic mock — backbone-dependent, 보통 $-2.0$ ~ $-0.1$
   범위 (실 LLM 측 토큰 logprob 평균).

본 mock 측 **deterministic** — 동일 (utterance, backbone_id) 측 동일 IntentResult.
paper §C 측 trial seed 재현성 정합.

## Naming

본 모듈 측 `_` prefix — *private* implementation. 외부 호출 측 `cloud_llm` /
`edge_llm` 측 subclass 측. 그러나 *test 측* 본 base 측 import 가능 (test 측
private 접근 OK convention).
"""

from __future__ import annotations

import hashlib
from typing import Mapping, Optional

from intent_llm.classifier import ClassifierWrapper
from intent_llm.interface import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    SIGNAL_LOGPROB,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
)


# Mock 신호 범위 — 실 LLM 측 자연 발생 범위 가정. backbone_id 측 mock variation
# 측 *결정론적* 측면 (SHA-256 측 [0, 1] uniform).
_MOCK_RHO_MIN: float = 0.70
_MOCK_RHO_MAX: float = 0.95
_MOCK_LOGPROB_MIN: float = -2.0
_MOCK_LOGPROB_MAX: float = -0.1

# Confidence boost 범위 — base classifier 측 ±0.1 변동. boost 측 ASK_USER
# fallback (c_raw=0.0) 측 *적용 안 함* — Tier 2 c_lo trigger 측 유지.
_CONFIDENCE_BOOST_MAGNITUDE: float = 0.1


def _hash_to_unit_interval(payload: str) -> float:
    """SHA-256 측 첫 4 byte big-endian uint32 → $[0, 1]$ uniform.

    eval_runner.seed_policy 측 *동일 패턴* 정합 — deterministic, portable.
    """
    digest = hashlib.sha256(payload.encode('utf-8')).digest()
    uint32 = int.from_bytes(digest[:4], byteorder='big', signed=False)
    return uint32 / (2**32 - 1)


def _scale(unit: float, lo: float, hi: float) -> float:
    """$[0, 1]$ → $[lo, hi]$ linear scaling."""
    return lo + unit * (hi - lo)


class _LLMMockBase:
    """Cloud / Edge LLM wrapper 공통 mock — IntentWrapper Protocol 충족.

    Attributes
    ----------
    category : str
        ADR-0018 D3 카테고리 — 'cloud_llm' | 'edge_llm'. subclass 측 잠금.
    identifier : str
        ADR-0014 D1 백본 식별자 — 'gpt-4o' · 'gemma-4-e4b' 등. 인스턴스 측 인자.
    """

    category: str = ''  # subclass 측 override

    def __init__(self, identifier: str) -> None:
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(
                f'identifier 빈 문자열 불가 — got {identifier!r}'
            )
        if not self.category:
            raise NotImplementedError(
                'subclass 측 category class attribute 정의 필요'
            )
        self.identifier = identifier
        # base classifier 측 delegation — keyword matching 측 fallback skill.
        # 실 LLM API call 측 후속 PR 측 본 라인 측 *swap*.
        self._classifier = ClassifierWrapper()

    def process(self, intent_input: IntentInput) -> IntentResult:
        """utterance → mock IntentResult (deterministic).

        Args:
            intent_input: IntentInput. context_graph 측 mock 측 *무시*
                (실 LLM wrapper 측 fusion logic 측 별 PR scope).

        Returns:
            IntentResult — typed_action + boosted confidence + ρ/ℓ mock signals.
        """
        base = self._classifier.process(intent_input)

        # ASK_USER fallback (c_raw=0.0) 측 boost 적용 안 함 — Tier 2 c_lo
        # trigger 측 유지 (PR #124 C-1 정정 정합).
        if base.confidence_raw == CONFIDENCE_MIN:
            return IntentResult(
                typed_action=base.typed_action,
                confidence_raw=base.confidence_raw,
                signals={
                    # s1(접지 엔트로피)은 OVD 전용 — LLM mock 미산출 (§2.1).
                    # ASK_USER fallback 측 ρ/ℓ 측 *낮은* mock — 실 LLM 측
                    # 모호한 발화 측 self-consistency 측 낮고 logprob 측 낮음.
                    SIGNAL_SELF_CONSISTENCY: _MOCK_RHO_MIN,
                    SIGNAL_LOGPROB: _MOCK_LOGPROB_MIN,
                },
            )

        # backbone + utterance 측 hash → deterministic mock variation.
        # backbone 별 *다른* hash → *다른* IntentResult 보장 (paper §C ablation
        # 측 6 백본 distinct 결과 정합).
        payload = f'{self.identifier}\x00{intent_input.utterance}\x00{intent_input.scenario_id}'
        u_conf = _hash_to_unit_interval(payload + '\x00conf')
        u_rho = _hash_to_unit_interval(payload + '\x00rho')
        u_logprob = _hash_to_unit_interval(payload + '\x00logprob')

        # confidence boost — ±_CONFIDENCE_BOOST_MAGNITUDE 측 base classifier
        # 측 변동. clip 측 [CONFIDENCE_MIN, CONFIDENCE_MAX].
        boost = (u_conf - 0.5) * 2.0 * _CONFIDENCE_BOOST_MAGNITUDE
        boosted = base.confidence_raw + boost
        confidence_raw = max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, boosted))

        # ρ · ℓ mock — backbone-dependent uniform range.
        rho = _scale(u_rho, _MOCK_RHO_MIN, _MOCK_RHO_MAX)
        logprob = _scale(u_logprob, _MOCK_LOGPROB_MIN, _MOCK_LOGPROB_MAX)

        signals: Mapping[str, Optional[float]] = {
            # s1(접지 엔트로피)은 OVD 전용 — LLM mock 미산출 (§2.1).
            SIGNAL_SELF_CONSISTENCY: rho,
            SIGNAL_LOGPROB: logprob,
        }

        return IntentResult(
            typed_action=base.typed_action,
            confidence_raw=confidence_raw,
            signals=signals,
        )
