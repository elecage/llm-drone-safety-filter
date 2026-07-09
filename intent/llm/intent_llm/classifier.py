"""Domain classifier wrapper — closed-vocabulary keyword matching 실 stub.

[ADR-0018 D3](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d3)
표 row 4:
> closed-vocabulary 분류기 (ADR-0013 D2 5 스킬 카탈로그에 한정) — stub 코드로
> 시작. 발화 텍스트 → 카탈로그 5개 스킬에 대한 softmax. 후보 점수 분포
> $\\{p_1, \\ldots, p_K\\}$ 와 인덱스 출력을 직접 IF 형식으로 제공. *별도 모델
> 학습 불필요* (zero-shot keyword matching 또는 small MiniLM 기반).

본 wrapper 측 paper §C 측 *동작 가능 의도해석기* 1 식별자 — Cloud / Edge / VLA
wrapper 측 후속 PR (실 LLM API call 필요) 측 *없이도* paper §C 측 ablation 측
1 카테고리 측 cover. RQ1 *어떤 의도해석기 입력에도 안전 보장* 측 직접 입증
자리 (cmsm-proof §2.1 T1·T2·T3 정합).

## Keyword matching logic

각 스킬 측 *trigger keyword set* 잠금:
  - `move_to`: 가자 / 가 줘 / 이동 / move / go
  - `inspect`: 봐 / 살펴 / 확인 / inspect / 보여 줘 / show
  - `return_to_dock`: 돌아 / 복귀 / return / dock
  - `emergency_land`: 비상 / 착륙 / land / emergency / 멈춰 / stop
  - `ask_user`: ? / 뭐 / 어떻게 / what / how / which

발화 측 각 keyword set 측 *match count* → softmax → 분포. argmax 측 skill,
max softmax 측 confidence_raw. 매치 0 측 fallback = *명시적 ASK_USER* + c_raw=0.0
+ signals entropy = $\\log K$ (PR #124 review C-1 정정 — 이전 implementation 측
uniform 분포 측 max() tie-break 측 ALL_SKILLS[0]=MOVE_TO 반환 = safety 위반.
[ADR-0013 D4](../../../docs/handover/decisions/0013-tier2-spec-lock.md#d4) 측
$c_\\text{lo}=0.4$ 미만 측 Tier 2 ask 자동 trigger 정합 측 safety-first design).

## Substring 매치 한계 (1차 시안)

본 stub 측 *zero-shot keyword substring matching* — 다음 false positive 가능:
  - 'go' keyword 측 'tango' / 'mango' 등 측 매치.
  - 한국어 keyword 측 *띄어쓰기 변동* 측 빠르게 깨짐 — '가 줘' (공백 있음) 측
    '가줘' (공백 없음) 측 매치 X.
  - 어형 변화 (활용형) 측 cover 안 됨 — '돌아' keyword 측 '돌아왔다' 측 매치
    되나 '돌아갈게' 측 다른 형식 측 매치 안 될 수 있음.

본 한계 측 paper §C 측 *Domain classifier* 측 baseline 한계 — Cloud/Edge LLM
wrapper 측 후속 PR 측 fluent NL parsing 측 비교 자리. RQ1 *어떤 의도해석기
입력에도 안전 보장* 측 classifier 측 false positive 측 *우리 안전 layer 측
변경 없이* 보장 측 입증 자리.

## 인자 채우기

본 stub 측 *skill identification* 만 cover — args 측 placeholder (예: ``move_to``
측 ``args = {}``). 실 args 채우기 (position 추출 등) 측 후속 PR 측 또는 paper
§C 측 *args 무시 측 안전 보장* (cmsm-proof §6 정형 정리 정합 — tier1 측 args
content 의존 X).

## 신호 산출

classifier 측 정본 신호(cmsm-proof §2.1) 중 *어느 것도 산출하지 않는다*:
s1(접지 엔트로피)은 **OVD 노드 전용**이고, s2(self-consistency)·s3(logprob)는
classifier 가 *deterministic* (M 회 추론 의미 없음 + 토큰 logprob 없음)이라 정의
안 됨. 따라서 `signals` 측 `SIGNAL_SELF_CONSISTENCY`·`SIGNAL_LOGPROB` = None,
s1 키 부재 → estimator 측 모두 부재 → fail-safe.
"""

from __future__ import annotations

import math
from typing import Mapping, Optional, Tuple

from intent_llm.interface import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    SIGNAL_LOGPROB,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    TypedAction,
)
from intent_llm.skill_catalog import ALL_SKILLS, SkillName


# 카테고리·식별자 — ADR-0018 D3 + 본 모듈 측 *유일* identifier.
CATEGORY: str = 'classifier'
IDENTIFIER: str = 'closed-vocabulary-keyword'


# Keyword set per skill — 한국어 + 영어 covers 양쪽. lowercase compare 측
# canonicalize. 본 set 측 확장 시 paper §C 측 영향 — 본 PR 측 1차 시안.
_SKILL_KEYWORDS: Mapping[SkillName, Tuple[str, ...]] = {
    SkillName.MOVE_TO: (
        '가자', '가 줘', '가줘', '이동', 'move', 'go', '가서',
        '날아', '날아가', '가봐', '앞으로', '뒤로', '왼쪽', '오른쪽',
        '위로', '아래로', 'forward', 'back', 'left', 'right', 'up', 'down',
    ),
    SkillName.INSPECT: (
        '봐', '살펴', '확인', '보여 줘', '보여줘', 'inspect', 'show', 'look',
        '촬영', '사진', '찍어',
    ),
    SkillName.RETURN_TO_DOCK: ('돌아', '복귀', 'return', 'dock'),
    SkillName.EMERGENCY_LAND: ('비상', '착륙', 'land', 'emergency', '멈춰', 'stop'),
    SkillName.ASK_USER: ('?', '뭐', '어떻게', 'what', 'how', 'which'),
}


def _count_matches(utterance: str, keywords: Tuple[str, ...]) -> int:
    """발화 측 keyword 측 match count — case-insensitive substring."""
    text = utterance.lower()
    return sum(1 for kw in keywords if kw.lower() in text)


def _softmax(
    scores: Tuple[float, ...], temperature: float = 1.0
) -> Tuple[float, ...]:
    """numerically stable softmax — subtract max + exp + normalize.

    호출자 측 *매치 0* 케이스 측 별 처리 (ASK_USER fallback) — 본 helper 측
    pure softmax 만.

    Args:
        scores: 비-uniform 입력 권장 (모든 0 측 호출자 측 거름).
        temperature: softmax temperature (양수). 기본 1.0.

    Returns:
        확률 분포 — sum 1.

    Raises:
        ValueError: temperature ≤ 0.
    """
    if temperature <= 0:
        raise ValueError(f'temperature={temperature} 양수 필수')
    scaled = [s / temperature for s in scores]
    max_s = max(scaled)
    exps = [math.exp(s - max_s) for s in scaled]
    total = sum(exps)
    return tuple(e / total for e in exps)


class ClassifierWrapper:
    """closed-vocabulary keyword matching wrapper — IntentWrapper Protocol 충족.

    paper §C 측 *동작 가능 의도해석기* 1 식별자 — ADR-0018 D3 정합.

    Note:
        본 wrapper 측 *deterministic* — 동일 utterance 측 동일 IntentResult.
        ablation 측 ask_user fallback (매치 0) 측 *낮은 c_raw + 높은 entropy*
        시그널 측 estimator + Tier 2 측 정상 처리 측 입증 자리.
    """

    category: str = CATEGORY
    identifier: str = IDENTIFIER

    def process(self, intent_input: IntentInput) -> IntentResult:
        """utterance → 5 스킬 측 softmax → argmax + entropy.

        Args:
            intent_input: IntentInput. context_graph 측 본 wrapper 측 *무시*
                (closed-vocabulary keyword matching 측 발화 만 사용).

        Returns:
            IntentResult.
        """
        # 1. 각 스킬 측 match count.
        scores: Tuple[float, ...] = tuple(
            float(_count_matches(intent_input.utterance, _SKILL_KEYWORDS[skill]))
            for skill in ALL_SKILLS
        )

        # 2. 매치 0 측 safety-first ASK_USER fallback — PR #124 review C-1 정정.
        # uniform 분포 측 argmax tie-break 측 ALL_SKILLS[0]=MOVE_TO 반환 = safety
        # 위반 (모호한 발화 측 침입 위험). ADR-0013 D4 c_lo=0.4 미만 측 Tier 2
        # ask 자동 trigger 정합 측 c_raw=0.0 + 명시적 ASK_USER.
        if all(s == 0.0 for s in scores):
            return IntentResult(
                typed_action=TypedAction(skill=SkillName.ASK_USER, args={}),
                confidence_raw=CONFIDENCE_MIN,
                signals={
                    # classifier(deterministic keyword)는 정본 신호 미산출 —
                    # s1=OVD 전용, s2/s3=deterministic 이라 정의 안 됨 (§2.1).
                    SIGNAL_SELF_CONSISTENCY: None,
                    SIGNAL_LOGPROB: None,
                },
            )

        # 3. softmax 분포.
        distribution = _softmax(scores)

        # 4. argmax → skill, max prob → confidence_raw.
        argmax_idx = max(range(len(distribution)), key=lambda i: distribution[i])
        chosen_skill = ALL_SKILLS[argmax_idx]
        confidence_raw = float(distribution[argmax_idx])
        # numerical safety — 부동소수점 측 [0, 1] 측 잘림 측 안전한 쪽.
        confidence_raw = max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, confidence_raw))

        # signals — classifier 는 정본 신호 중 어느 것도 산출 안 함 (§2.1):
        # s1=OVD 전용, s2(self-consistency)·s3(logprob)는 deterministic 이라
        # 정의 안 됨. estimator 측 모두 부재 → fail-safe.
        signals: Mapping[str, Optional[float]] = {
            SIGNAL_SELF_CONSISTENCY: None,
            SIGNAL_LOGPROB: None,
        }

        return IntentResult(
            typed_action=TypedAction(skill=chosen_skill, args={}),
            confidence_raw=confidence_raw,
            signals=signals,
        )
