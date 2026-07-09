"""의도해석기 공통 contract — TypedAction · IntentInput · IntentResult · IntentWrapper.

[intent_layer_theory §3.1](../../../docs/research_notes/intent_layer_theory.md)
측 인터페이스 IF 정합:
  - OVD 측 후보 점수 분포 $\\{p_1, \\ldots, p_K\\} \\in \\Delta^{K-1}$ 산출.
  - LLM 측 (a) $M$ 회 독립 추론 후보 인덱스 시퀀스 + (b) 각 추론 측 토큰 로그확률
    산출.

본 인터페이스 측 모든 wrapper (Cloud LLM · Edge LLM · VLA · Classifier ·
Adversarial) 측 충족 의무. wrapper 측 차이 = 내부 구현 (실 LLM API · 실 OVD ·
keyword matching 등). estimator 측 본 wrapper 산출 측 IntentResult 측 c 추정 +
EstimatorReport 산출 ([ADR-0020](../../../docs/handover/decisions/0020-confidence-estimator-g-form-lock.md)
정합).

## 신호 셋 (cmsm-proof §2.1 정본 정합)

cmsm-proof §2.1 원시 신호 $(s_1, s_2, s_3) = (H, \\rho, \\ell)$:
  - $H$ (s1) — *접지 엔트로피*. OVD 점수 분포 $\\{p_1, \\ldots, p_K\\}$ 의 정규화
    섀넌 entropy. **OVD 노드 전용 산출** (`/intent/ovd_candidates`) — *의도해석기
    wrapper 는 s1 을 산출하지 않는다*. LLM/classifier wrapper 의 signals 에는 s1
    키가 부재 → estimator 측 ``s1_absent`` (fail-safe-by-construction).
  - $\\rho$ (s2) — *자기일관성*. LLM 을 $M$ 회 독립 추론해 **OVD 후보 인덱스**를
    선택할 때 1·2위 빈도 격차 $(n_1 - n_2) / M$ (LLM 산출, classifier 측 deterministic
    이라 정의 안 됨). 정본 s2 는 OVD 후보 입력 의존 — OVD 미연결 단계는 *skill-level
    self-consistency proxy* (정본 referent-index 정렬은 OVD 연결 시,
    [ADR-0020 amendment](../../../docs/handover/decisions/0020-confidence-estimator-g-form-lock.md)).
  - $\\ell$ (s3) — 토큰 로그확률 기하평균 (LLM 측 산출, classifier 측 정의 안 됨).

본 contract 측 ``signals: Mapping[str, Optional[float]]`` — wrapper 측 지원
신호만 채움. **s1 은 어떤 wrapper 도 산출하지 않는다 (OVD 전용)** — LLM/VLA 측
ρ/ℓ, classifier 측 채우는 정본 신호 없음. estimator 측 부재 신호 fallback
(ADR-0020 D3 fail-safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from intent_llm.skill_catalog import SkillName


# 신뢰도 raw 측 단위 구간 $[0, 1]$. 본 모듈 외 다른 모듈도 본 상수 측 참조 측
# *single source-of-truth* — magic value (M-4 lesson 정합).
CONFIDENCE_MIN: float = 0.0
CONFIDENCE_MAX: float = 1.0

# 신호 키 — cmsm-proof §2.1 정합. wrapper 측 IntentResult.signals 측 본 키 측
# 사용. 다른 키 사용 측 estimator 측 fallback 측 KeyError (defensive).
SIGNAL_ENTROPY = 's1_entropy'  # H — OVD 점수 분포 entropy (OVD 노드 전용, wrapper 미산출)
SIGNAL_SELF_CONSISTENCY = 's2_self_consistency'  # rho — LLM 의 OVD 후보 인덱스 self-consistency
SIGNAL_LOGPROB = 's3_logprob'  # ell — 토큰 로그확률 기하평균
# s3 *구조적* 능력 플래그 (ADR-0020 D8) — bool. False 면 백본이 token logprob 을
# 원천적으로 못 냄 (edge ollama) → 소비자(estimator)가 s3 를 곱에서 *제외*(neutral).
# True (또는 키 부재) 면 logprob 가용 → s3_logprob 값(또는 런타임 부재)으로 처리.
SIGNAL_S3_CAPABILITY = 's3_capability'

# IntentResult.signals 측 허용 키 set — wrapper 측 typo (예: 's1_entrpy') 측
# silent corruption 차단. PR #124 review M-1 정합.
VALID_SIGNAL_KEYS = frozenset({
    SIGNAL_ENTROPY, SIGNAL_SELF_CONSISTENCY, SIGNAL_LOGPROB, SIGNAL_S3_CAPABILITY,
})


@dataclass(frozen=True)
class TypedAction:
    """ADR-0013 D2 5 스킬 카탈로그 측 typed action — wrapper 측 출력 잠금.

    Attributes
    ----------
    skill : SkillName
        ADR-0013 D2 5 스킬 중 하나.
    args : Mapping[str, Any]
        skill-specific 인자 dict. 예: ``move_to`` 측 ``{'position': (x, y, z),
        'max_speed': 0.3}``. args 측 *content 검증* 측 본 모듈 측 안 함 —
        Tier 2 게이트 측 schema 검증 ([ADR-0013 D3](../../../docs/handover/decisions/0013-tier2-spec-lock.md#d3)).
    """

    skill: SkillName
    args: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.skill, SkillName):
            raise TypeError(
                f'skill 은 SkillName 여야 함, got {type(self.skill).__name__}'
            )


@dataclass(frozen=True)
class IntentInput:
    """wrapper 측 입력 — 사용자 발화 + scenario context + (선택) context graph.

    Attributes
    ----------
    utterance : str
        사용자 발화 텍스트 ([ADR-0018 D1](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d1)
        text-direct 정합).
    scenario_id : str
        시나리오 식별자 — 'S5' · 'S6' · 'S7' · 'S8' (ADR-0006 indoor 4 정합).
    context_graph : Optional[Mapping[str, Any]]
        context augmentation graph (paper §6 fusion 입력). None 측 wrapper 측
        utterance 만 사용 — *direct* mode. dict 측 wrapper 측 fusion mode.
    """

    utterance: str
    scenario_id: str
    context_graph: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.utterance, str) or not self.utterance.strip():
            raise ValueError(
                f'utterance 빈 문자열 불가 — got {self.utterance!r}'
            )
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise ValueError(
                f'scenario_id 빈 문자열 불가 — got {self.scenario_id!r}'
            )


@dataclass(frozen=True)
class IntentResult:
    """wrapper 측 출력 — TypedAction σ_raw + confidence c_raw + signals dict.

    Attributes
    ----------
    typed_action : TypedAction
        ADR-0013 D2 5 스킬 중 하나 + args.
    confidence_raw : float
        의도 해석 신뢰도 raw — $[0, 1]$. estimator 측 본 raw 값 측 추가 정규화
        + 변화율 제한기 측 $\\tilde c$ 산출 ([ADR-0020](../../../docs/handover/decisions/0020-confidence-estimator-g-form-lock.md)
        정합).
    signals : Mapping[str, Optional[float]]
        원시 신호 dict — keys ∈ {SIGNAL_ENTROPY, SIGNAL_SELF_CONSISTENCY,
        SIGNAL_LOGPROB, SIGNAL_S3_CAPABILITY}. wrapper 측 지원 신호만 채움 (예:
        classifier 측 H 만, LLM 측 H/ρ/ℓ 모두). 미지원 신호 측 None 또는 키 부재.
        SIGNAL_S3_CAPABILITY 만 *bool* (ADR-0020 D8 구조적 능력 플래그) — 나머지는
        float. False 면 s3 *구조적 부재* (백본 logprob 무능력), True/부재 면 가용.
    """

    typed_action: TypedAction
    confidence_raw: float
    signals: Mapping[str, Optional[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.typed_action, TypedAction):
            raise TypeError(
                f'typed_action 은 TypedAction 여야 함, '
                f'got {type(self.typed_action).__name__}'
            )
        if not isinstance(self.confidence_raw, (int, float)) or isinstance(
            self.confidence_raw, bool
        ):
            raise TypeError(
                f'confidence_raw 는 float 여야 함, '
                f'got {type(self.confidence_raw).__name__}'
            )
        if not (CONFIDENCE_MIN <= float(self.confidence_raw) <= CONFIDENCE_MAX):
            raise ValueError(
                f'confidence_raw={self.confidence_raw} 범위 위반 — '
                f'[{CONFIDENCE_MIN}, {CONFIDENCE_MAX}] 필수'
            )
        # PR #124 review M-1 — signals 측 unknown key 측 silent corruption 차단.
        # estimator 측 fallback 측 KeyError 측 늦은 발견 회피.
        unknown_keys = set(self.signals.keys()) - VALID_SIGNAL_KEYS
        if unknown_keys:
            raise ValueError(
                f'signals 측 unknown keys: {sorted(unknown_keys)!r} — '
                f'허용 = {sorted(VALID_SIGNAL_KEYS)!r}. '
                f'wrapper 측 typo 또는 schema 외 키 의심.'
            )


@runtime_checkable
class IntentWrapper(Protocol):
    """공통 *의도해석기* wrapper interface — runtime_checkable Protocol.

    모든 wrapper (CloudLLMWrapper · EdgeLLMWrapper · VLAWrapper ·
    ClassifierWrapper · AdversarialWrapper) 측 본 Protocol 측 측 충족.

    Attributes
    ----------
    category : str
        ADR-0018 D3 카테고리 식별자 — 'cloud_llm' · 'edge_llm' · 'vla' ·
        'classifier' · 'adversarial'.
    identifier : str
        카테고리 내 식별자 — 예: 'gpt-4o' · 'gemma-4-e4b' · 'openvla-7b' ·
        'closed-vocabulary' · 'gpt-4o-injected'.
    """

    category: str
    identifier: str

    def process(self, intent_input: IntentInput) -> IntentResult:
        """utterance + scenario + (optional) context_graph → IntentResult.

        Args:
            intent_input: IntentInput.

        Returns:
            IntentResult — TypedAction σ_raw + c_raw + signals.

        Note:
            본 Protocol 측 *exception 정책* 별 명시 안 함 — wrapper 측 *어떠한*
            입력 측 항상 *유효* IntentResult 산출 의무 (paper §C RQ1 *어떤
            의도해석기 입력에도 안전 보장* 정합). 실패 측 fallback (예: ask_user
            + c_raw=0.0) 측 wrapper 측 책임.
        """
        ...
