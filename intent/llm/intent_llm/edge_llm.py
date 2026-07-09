"""Edge LLM wrapper — 실 Ollama HTTP API call (C14 swap).

[ADR-0014 D1](../../../docs/handover/decisions/0014-llm-backbone-six-lock.md#d1)
local 3 백본 (Gemma 4 E4B · Qwen2.5-VL 7B · Llama 3.2 11B-Vision).
Backend: Ollama HTTP API (llama.cpp Metal wrapper, ADR-0014 D2 잠금).

## 환경변수

- OLLAMA_BASE_URL  (선택, 기본값 http://localhost:11434)
- TRIAL_LOG_DIR    (선택) — 존재 시 `edge_llm_<identifier>.jsonl` 로그 저장.

## Ollama 모델 태그

| 내부 식별자 | Ollama pull 태그 |
|---|---|
| gemma-4-e4b | gemma4:e4b |
| qwen2.5-vl-7b | qwen2.5-vl:7b |
| llama-3.2-11b-vision | llama3.2-vision:11b |

태그는 C33 (Mac mini 양자화 환경 검증) 완료 시 실 설치 후 검증 필요.

## 예외 정책

process() 는 **항상** 유효한 IntentResult 를 반환 (interface.py 계약).
- Ollama 연결 오류 / 응답 오류 → ASK_USER fallback.
- requests 패키지 미설치 → ImportError 전파.
- Ollama 서비스 미기동 → ConnectionError 계열 → ASK_USER fallback.

## logprob 제약

Ollama HTTP API 는 token-level logprob 표준 지원 없음. s3 (SIGNAL_LOGPROB)
는 고정 fallback 값 사용 — C33 완료 후 llama.cpp logprobs 옵션 검토 예정.

## vllm-mlx deferred

ADR-0014 D2 — paper §C 단일 요청 직렬 trial 측 Ollama 1차 권장.
vllm-mlx (paper-2 multi-user serving) 재검토 후보.
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
CATEGORY: str = 'edge_llm'

# Ollama 기본 URL. OLLAMA_BASE_URL env 로 재정의 가능.
_OLLAMA_DEFAULT_URL: str = 'http://localhost:11434'

# ADR-0014 D1 → Ollama pull 태그 매핑. C33 완료 시 실 설치 검증.
_OLLAMA_MODEL_MAP: Dict[str, str] = {
    'llama-3.2-11b-vision': 'llama3.2-vision:11b',
    'qwen2.5-vl-7b': 'qwen2.5-vl:7b',
    'gemma-4-e4b': 'gemma4:e4b',
}

# ADR-0020 D2 — self-consistency M 회 독립 호출.
_M_SELF_CONSISTENCY: int = 3

_TEMPERATURE: float = 0.7
_MAX_TOKENS: int = 256
_TIMEOUT_S: int = 60

# ADR-0020 D8 — Ollama 는 token logprob 을 *원천적으로* 못 냄 (구조적 부재).
# 종전 sentinel(-2.0) → exp → 0.135 *상수* 천장 경로 폐기. s3_logprob=None +
# s3_capability=False 명시 → 소비자(estimator)가 s3 를 곱에서 제외(neutral) →
# edge c = s1·s2 로 정상 변조 (C1 LLM-불가지 보전).
# trial log historical 표식 (정상 signals 에선 미사용 — None 발행).
_OLLAMA_LOGPROB_FALLBACK: float = -2.0

# API 오류 시 fallback 신호 — 최대 불확실 상태 표현 (s2=0 → c=0).
# s1(접지 엔트로피)은 OVD 노드 전용이라 wrapper signals 에 미포함 (정본 §2.1).
_ERROR_SIGNALS = {
    SIGNAL_SELF_CONSISTENCY: 0.0,
    SIGNAL_LOGPROB: None,
    SIGNAL_S3_CAPABILITY: False,  # edge 구조적 부재 (s2=0 이라 c=0 무관, 일관성)
}


class EdgeLLMWrapper(_LLMMockBase):
    """Ollama local LLM wrapper — 실 HTTP call (C14).

    ADR-0014 D1 local 3 백본 wiring. _LLMMockBase 상속 측 IntentWrapper Protocol
    + registry.py 측 isinstance 검증 유지. process() override 측 Ollama HTTP.

    Ollama 서비스 미기동 / 연결 오류 → ASK_USER fallback (raise 안 함).
    requests 패키지 미설치 → ImportError 전파 (설치 오류는 운용자 처리).
    """

    category: str = CATEGORY

    def __init__(self, identifier: str) -> None:
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f'identifier 빈 문자열 불가 — got {identifier!r}')
        if identifier not in _OLLAMA_MODEL_MAP:
            raise ValueError(
                f'unknown edge backbone: {identifier!r}. '
                f'허용: {sorted(_OLLAMA_MODEL_MAP)!r}'
            )
        self.identifier = identifier
        self._model_tag = _OLLAMA_MODEL_MAP[identifier]

    def process(self, intent_input: IntentInput) -> IntentResult:
        """M=3 Ollama HTTP 호출 → ρ/H/ℓ 신호 + TypedAction.

        Ollama 연결 오류 시 ASK_USER fallback.
        """
        import requests  # ImportError 측 호출자 전파 (미설치 = 운용 오류)

        base_url = os.environ.get('OLLAMA_BASE_URL', _OLLAMA_DEFAULT_URL).rstrip('/')
        endpoint = f'{base_url}/api/chat'

        messages = build_messages(
            intent_input.utterance,
            intent_input.scenario_id,
            intent_input.context_graph,
        )

        try:
            skills: List[SkillName] = []
            contents: Dict[SkillName, str] = {}

            # RQ3(LLM 지연 독립성, ADR-0039 D3-②) — M=3 호출 + 신호 계산 전체를
            # inference latency 로 측정. perf_counter(단조)라 백본 간 *상대* 분포
            # 비교 정합(tier1 cadence 와 병치). edge(ollama)는 cloud 대비 지연 큼.
            t_start = time.perf_counter()
            for _ in range(_M_SELF_CONSISTENCY):
                payload = {
                    'model': self._model_tag,
                    'messages': messages,
                    'stream': False,
                    'format': 'json',
                    # reasoning 모델(gemma4 등)은 chain-of-thought 를 먼저 내보내
                    # num_predict 를 소진하고 content 가 빈 채 length 로 끊긴다.
                    # think=False 로 reasoning 을 끄면 content 가 직접 JSON.
                    'think': False,
                    'options': {
                        'temperature': _TEMPERATURE,
                        'num_predict': _MAX_TOKENS,
                    },
                }
                resp = requests.post(endpoint, json=payload, timeout=_TIMEOUT_S)
                resp.raise_for_status()
                content = resp.json().get('message', {}).get('content', '')
                action = parse_typed_action(content)
                skills.append(action.skill)
                contents[action.skill] = content

            majority_skill, rho = majority_vote(skills)
            # h = skill 분포 entropy — *진단/trial-log 전용* (정본 s1 아님).
            # 정본 s1(접지 엔트로피)은 OVD 점수 분포 기반·OVD 노드 산출 (§2.1).
            h = compute_skill_entropy(skills)

            final_action = parse_typed_action(contents.get(majority_skill, ''))
            confidence_raw = (
                CONFIDENCE_MIN
                if final_action.skill == SkillName.ASK_USER
                else max(CONFIDENCE_MIN, min(1.0, rho))
            )

            inference_latency_s = time.perf_counter() - t_start
            _write_trial_log(
                self._model_tag, messages, skills, rho, h, None,
                inference_latency_s,
            )

            return IntentResult(
                typed_action=final_action,
                confidence_raw=confidence_raw,
                # s1(접지 엔트로피)은 OVD 전용 → wrapper signals 에 미포함 (§2.1).
                # estimator 측 s1_absent → c=0 (OVD 미연결 시 fail-safe).
                # s3: ADR-0020 D8 구조적 부재 (ollama logprob 무능력) → 명시 플래그.
                signals={
                    SIGNAL_SELF_CONSISTENCY: rho,
                    SIGNAL_LOGPROB: None,
                    SIGNAL_S3_CAPABILITY: False,
                },
            )

        except Exception as exc:
            _LOG.warning(
                'edge_llm %s Ollama 오류 → ASK_USER fallback: %s',
                self._model_tag,
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
    model_tag: str,
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
        safe_tag = model_tag.replace(':', '_').replace('/', '_')
        log_path = os.path.join(log_dir, f'edge_llm_{safe_tag}.jsonl')
        entry = {
            'timestamp': time.time(),
            'model': model_tag,
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
