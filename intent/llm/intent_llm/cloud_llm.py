"""Cloud LLM wrapper — 실 OpenAI API call (C14 swap).

[ADR-0014 D1](../../../docs/handover/decisions/0014-llm-backbone-six-lock.md#d1)
cloud 3 백본 (GPT-4o · GPT-5 · GPT-5.5). ADR-0014 D2/D3 재현성 요건:
  - 캐싱 비활성화 (seed=None — 동일 prompt ≠ 동일 응답).
  - 정확한 model identifier (ADR-0014 D3 supplementary log 정합).
  - TRIAL_LOG_DIR env 설정 시 API 호출 JSONL 로그 저장.

## 환경변수

- OPENAI_API_KEY  (필수) — paper §C 운용 전 설정.
- TRIAL_LOG_DIR   (선택) — 존재 시 `cloud_llm_<identifier>.jsonl` 로그 저장.

## 예외 정책

process() 는 **항상** 유효한 IntentResult 를 반환 (interface.py 계약).
- API 통신 오류 → ASK_USER fallback (c_raw=0.0, 최대 불확실 신호).
- openai 패키지 미설치 → ImportError 전파 (설치 오류는 운용자 처리).
- OPENAI_API_KEY 미설정 → RuntimeError 전파 (설정 오류는 운용자 처리).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

from intent_llm._llm_mock import _LLMMockBase
from intent_llm._llm_prompt import (
    build_messages,
    compute_skill_entropy,
    majority_vote,
    parse_typed_action,
)
from intent_llm.interface import (
    CONFIDENCE_MIN,
    SIGNAL_LOGPROB,
    SIGNAL_S3_CAPABILITY,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    TypedAction,
)
from intent_llm.skill_catalog import SkillName

_LOG = logging.getLogger(__name__)

# ADR-0018 D3 카테고리 식별자.
CATEGORY: str = 'cloud_llm'

# ADR-0014 D1 → 실 OpenAI model identifier 매핑.
_OPENAI_MODEL_MAP: Dict[str, str] = {
    'gpt-4o': 'gpt-4o',
    'gpt-5': 'gpt-5',
    'gpt-5.5': 'gpt-5.5',
}

# ADR-0020 D2 — self-consistency M 회 독립 호출.
_M_SELF_CONSISTENCY: int = 3

_TEMPERATURE: float = 0.7
_MAX_TOKENS: int = 256

# API 오류 시 fallback 신호 — 최대 불확실 상태 표현 (s2=0 → c=0).
# s1(접지 엔트로피)은 OVD 노드 전용이라 wrapper signals 에 미포함 (정본 §2.1).
# cloud 는 logprob *능력 보유* (logprobs=True) → s3_capability=True. 단 s2=0 이라
# c=0 (ADR-0020 D8 — 능력 부재가 아닌 에러 상황).
_ERROR_SIGNALS = {
    SIGNAL_SELF_CONSISTENCY: 0.0,
    SIGNAL_LOGPROB: None,
    SIGNAL_S3_CAPABILITY: True,
}


class CloudLLMWrapper(_LLMMockBase):
    """OpenAI cloud LLM wrapper — 실 API call (C14).

    ADR-0014 D1 cloud 3 백본 wiring. _LLMMockBase 상속 측 IntentWrapper Protocol
    + registry.py 측 isinstance 검증 유지. process() override 측 실 OpenAI API.

    OPENAI_API_KEY 미설정 → RuntimeError (process() 에서 raise).
    openai 패키지 미설치 → ImportError (process() 에서 raise).
    API 통신 오류 → ASK_USER fallback (raise 안 함).
    """

    category: str = CATEGORY

    def __init__(self, identifier: str) -> None:
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f'identifier 빈 문자열 불가 — got {identifier!r}')
        if identifier not in _OPENAI_MODEL_MAP:
            raise ValueError(
                f'unknown cloud backbone: {identifier!r}. '
                f'허용: {sorted(_OPENAI_MODEL_MAP)!r}'
            )
        self.identifier = identifier
        self._model_id = _OPENAI_MODEL_MAP[identifier]

    def process(self, intent_input: IntentInput) -> IntentResult:
        """M=3 OpenAI API 호출 → ρ/H/ℓ 신호 + TypedAction.

        API 통신 오류 시 ASK_USER fallback.
        """
        import openai  # ImportError 측 호출자 전파 (미설치 = 운용 오류)

        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError(
                'OPENAI_API_KEY 환경변수 미설정 — paper §C 실험 전 설정 필요 '
                '(ADR-0014 D3)'
            )

        messages = build_messages(
            intent_input.utterance,
            intent_input.scenario_id,
            intent_input.context_graph,
        )

        try:
            client = openai.OpenAI(api_key=api_key)
            skills: List[SkillName] = []
            contents: Dict[SkillName, str] = {}
            logprob_samples: List[float] = []

            # RQ3(LLM 지연 독립성, ADR-0039 D3-②) — M=3 self-consistency 호출 +
            # 신호 계산 전체를 inference latency 로 측정. perf_counter(단조)라
            # wall-clock 과 무관하게 백본 간 *상대* 분포 비교 정합(tier1 cadence 와 병치).
            t_start = time.perf_counter()
            for _ in range(_M_SELF_CONSISTENCY):
                resp = client.chat.completions.create(
                    model=self._model_id,
                    messages=messages,
                    response_format={'type': 'json_object'},
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                    logprobs=True,
                    top_logprobs=5,
                    seed=None,  # 캐싱 비활성화 (ADR-0014 D2)
                )
                choice = resp.choices[0]
                content = choice.message.content or ''
                action = parse_typed_action(content)
                skills.append(action.skill)
                contents[action.skill] = content

                if choice.logprobs and choice.logprobs.content:
                    lps = [
                        t.logprob
                        for t in choice.logprobs.content
                        if t.logprob is not None
                    ]
                    if lps:
                        logprob_samples.append(sum(lps) / len(lps))

            majority_skill, rho = majority_vote(skills)
            # h = skill 분포 entropy — *진단/trial-log 전용* (정본 s1 아님).
            # 정본 s1(접지 엔트로피)은 OVD 점수 분포 기반·OVD 노드 산출 (§2.1).
            h = compute_skill_entropy(skills)
            # ADR-0020 D8 — cloud 는 logprob 능력 보유. 토큰 미반환(partial 실패)은
            # *런타임* 부재(None) → 소비자 s3:=0 → c=0 fail-safe (구조적 부재 아님).
            logprob = (
                sum(logprob_samples) / len(logprob_samples)
                if logprob_samples
                else None
            )

            final_action = parse_typed_action(contents.get(majority_skill, ''))
            confidence_raw = (
                CONFIDENCE_MIN
                if final_action.skill == SkillName.ASK_USER
                else max(CONFIDENCE_MIN, min(1.0, rho))
            )

            inference_latency_s = time.perf_counter() - t_start
            _write_trial_log(
                self._model_id, messages, skills, rho, h, logprob,
                inference_latency_s,
            )

            return IntentResult(
                typed_action=final_action,
                confidence_raw=confidence_raw,
                # s1(접지 엔트로피)은 OVD 전용 → wrapper signals 에 미포함 (§2.1).
                # estimator 측 s1_absent → c=0 (OVD 미연결 시 fail-safe).
                # s3: cloud 는 logprob 능력 보유 → s3_capability=True (값 None=런타임 부재).
                signals={
                    SIGNAL_SELF_CONSISTENCY: rho,
                    SIGNAL_LOGPROB: logprob,
                    SIGNAL_S3_CAPABILITY: True,
                },
            )

        except Exception as exc:
            _LOG.warning(
                'cloud_llm %s API 오류 → ASK_USER fallback: %s',
                self._model_id,
                exc,
            )
            return _error_fallback()


def _error_fallback() -> IntentResult:
    return IntentResult(
        typed_action=TypedAction(
            skill=SkillName.ASK_USER,
            args={'question': 'I encountered an error. Please try again.'},
        ),
        confidence_raw=CONFIDENCE_MIN,
        signals=dict(_ERROR_SIGNALS),
    )


def _write_trial_log(
    model_id: str,
    messages: List[Dict],
    skills: List[SkillName],
    rho: float,
    h: float,
    logprob: Optional[float],
    inference_latency_s: float,
) -> None:
    """TRIAL_LOG_DIR 설정 시 JSONL 로그 저장 (ADR-0014 D3 재현성 + RQ3 latency).

    inference_latency_s: M=3 호출 + 신호 계산 소요 [s] (perf_counter, ADR-0039
    D3-②). 백본별 분포를 tier1 setpoint cadence 와 병치해 LLM 지연 독립성 입증.
    """
    log_dir = os.environ.get('TRIAL_LOG_DIR', '')
    if not log_dir:
        return
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f'cloud_llm_{model_id}.jsonl')
        entry = {
            'timestamp': time.time(),
            'model': model_id,
            'messages': messages,
            'skills': [s.value for s in skills],
            'rho': rho,
            'entropy': h,
            'logprob': logprob,
            'inference_latency_s': inference_latency_s,
        }
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as exc:
        _LOG.warning('trial log 저장 실패: %s', exc)
