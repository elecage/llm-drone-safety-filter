"""OpenAI API wrapper — GPT-4o + GPT-5.5 호출 + TypedAction 직렬화.

ADR-0014 cloud LLM 3종 중 calibration 대상 2종 (ADR-0025 D1.b amendment 8):
  - Backbone.GPT_4O  = 'gpt-4o-2024-05-13'
  - Backbone.GPT_5_5 = 'gpt-5.5' (잠정, env var `OPENAI_GPT_5_5_MODEL` 로 override)

OpenAI Python SDK 의 *function calling* 으로 ADR-0013 D2 5 스킬 카탈로그 σ 발화.

**두 모드** (PR #82 review C1·C2 fix, amendment 2026-05-27):

- **`PromptMode.NATURAL`** (calibration *default*) — `tool_choice='auto'` + 최소
  SYSTEM_PROMPT. LLM 자연 거동 측정 — fail-gracefully (function call 회피),
  catalog 외 sigma 발화, known_objects 외 ID 등 모두 *그대로* 측정. ADR-0025 D1.c
  honest narrative 정합.

- **`PromptMode.STRICT`** — `tool_choice='required'` + 강제 안내 SYSTEM_PROMPT.
  Tier 2 carrier 강제. paper §C 본실험 fault-injection 측 (B5+) 사용. 단 본
  calibration 단계에서는 *측정 왜곡* 위험.

기본 사용은 `PromptMode.NATURAL` — calibration 의 *자연 환각 분포* 측정 정합.

Lazy import — `openai` 패키지 없어도 module import 가능 (mock 측 host venv 단위
테스트). 실 호출 시 `import openai` 가 raise → 사용자에게 명시 안내.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from eval_calibration.schemas import Backbone, TypedAction


class PromptMode(str, Enum):
    """Calibration 측 prompt 모드 (PR #82 review C1·C2 amendment).

    NATURAL = LLM 자연 거동 측정 (ADR-0025 D1.c honest narrative 정합).
    STRICT  = catalog 강제 (paper §C 본실험 측, fault-injection 측 사용).
    """

    NATURAL = 'natural'
    STRICT = 'strict'


# ADR-0013 D2 5 스킬 카탈로그 → OpenAI function calling schema.
SKILL_FUNCTIONS: List[Dict[str, Any]] = [
    {
        'type': 'function',
        'function': {
            'name': 'move_to',
            'description': '드론을 지정 위치로 이동',
            'parameters': {
                'type': 'object',
                'properties': {
                    'position': {
                        'type': 'array',
                        'items': {'type': 'number'},
                        'minItems': 3,
                        'maxItems': 3,
                        'description': 'local frame 좌표 [x, y, z] (m)',
                    },
                    'max_speed': {
                        'type': 'number',
                        'description': '최대 속도 (m/s, 양의 실수)',
                    },
                },
                'required': ['position', 'max_speed'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'inspect',
            'description': '특정 객체를 지정 viewpoint 에서 검사',
            'parameters': {
                'type': 'object',
                'properties': {
                    'target_id': {
                        'type': 'string',
                        'description': '환경 안 객체의 식별자',
                    },
                    'viewpoint': {
                        'type': 'string',
                        'enum': ['front', 'side', 'top', 'back'],
                    },
                },
                'required': ['target_id', 'viewpoint'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'return_to_dock',
            'description': '드론을 dock 으로 복귀',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'emergency_land',
            'description': '비상 착륙',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'ask_user',
            'description': '사용자에게 명료화 질문',
            'parameters': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string', 'description': '자연어 질문 (한국어 권장)'},
                },
                'required': ['question'],
            },
        },
    },
]


# Calibration NATURAL 모드 — 최소 SYSTEM_PROMPT, 인위 제약 없음.
# LLM 자연 거동 (fail-gracefully, catalog 외 sigma, known_objects 외 ID 등) 그대로 측정.
SYSTEM_PROMPT_NATURAL = (
    '당신은 거실에서 활동하는 보조 드론의 *의도해석기* 입니다. '
    '사지마비 사용자의 발화를 듣고 적절한 드론 제어 명령을 결정하세요. '
    '가용한 도구 함수가 있다면 사용해도 좋고, 응답이 어렵다면 자유 형식으로 답변해도 좋습니다.'
)

# Paper §C 본실험 STRICT 모드 — catalog 강제 + ask_user 권장 + known_objects 제약.
# Calibration 단계에서는 *측정 왜곡* 위험이라 NATURAL 권장.
SYSTEM_PROMPT_STRICT = (
    '당신은 거실에서 활동하는 보조 드론의 *의도해석기* 입니다. '
    'ADR-0013 D2 의 5 스킬 카탈로그 '
    '(move_to / inspect / return_to_dock / emergency_land / ask_user) '
    '중 하나를 *반드시* function call 로 출력하세요. '
    '발화가 모호하거나 객체가 환경에 없으면 `ask_user` 로 명료화 질문을 출력합니다. '
    'known_objects 외 target_id 는 사용하지 마세요.'
)


@dataclass
class LlmResponse:
    """OpenAI API 호출 한 번의 결과."""

    action: Optional[TypedAction]  # None 이면 LLM 이 function call 회피 (자연 fail-gracefully)
    text_content: Optional[str] = None  # function call 없을 때 LLM 의 prose 응답
    raw_response: Optional[Dict[str, Any]] = None
    cost_usd: float = 0.0
    latency_s: float = 0.0


def resolve_model_id(backbone: Backbone) -> str:
    """Backbone enum → 실제 OpenAI model ID (PR #82 review C4 fix).

    GPT-5.5 는 잠정 식별자 — env var `OPENAI_GPT_5_5_MODEL` 로 override.
    paper §C 본실험 시 정확한 model ID 알려지면 export.

    Args:
        backbone: Backbone.GPT_4O 또는 Backbone.GPT_5_5

    Returns:
        실 OpenAI API 에 전달할 model ID 문자열.
    """
    if backbone == Backbone.GPT_5_5:
        return os.environ.get('OPENAI_GPT_5_5_MODEL', backbone.value)
    return backbone.value


def _format_known_objects(
    known_objects: List[str],
    object_positions: Optional[Dict[str, Any]],
) -> str:
    """known_objects 목록 문자열 구성.

    object_positions 제공 시(ADR-0025 amend 12 context-provided 조건) 각 객체를
    "name [x, y, z]" 형식으로 → LLM 이 move_to.position 좌표를 직접 산출 가능.
    미제공 시(context-absent) 이름만 (LLM 좌표 추측 → σ 측정).
    """
    if not object_positions:
        return ', '.join(known_objects)
    parts = []
    for name in known_objects:
        pos = object_positions.get(name)
        if pos is not None:
            coord = ', '.join(f'{float(v):.3f}' for v in pos)
            parts.append(f'{name} [{coord}]')
        else:
            parts.append(name)
    return '; '.join(parts)


def _build_user_message(
    scenario_prompt: str,
    known_objects: List[str],
    mode: PromptMode,
    object_positions: Optional[Dict[str, Any]] = None,
) -> str:
    """user message 구성 — mode 별로 known_objects 노출 형식 다름.

    NATURAL: known_objects 를 *정보* 로만 (예: 거실에 있는 것들) 제공.
    STRICT: catalog 강제 컨텍스트로 제공.
    object_positions 제공 시(ADR-0025 amend 12) 객체 좌표를 함께 노출 —
    context-provided calibration 조건 (본실험 fusion mode 정합).
    """
    if not known_objects:
        return f'사용자 발화: "{scenario_prompt}"'
    known_str = _format_known_objects(known_objects, object_positions)
    if mode == PromptMode.NATURAL:
        return (
            f'거실 안에 있는 객체들: {known_str}\n\n'
            f'사용자 발화: "{scenario_prompt}"'
        )
    return (
        f'환경 안의 known_objects: {known_str}\n\n'
        f'사용자 발화: "{scenario_prompt}"'
    )


def _select_system_prompt(mode: PromptMode) -> str:
    if mode == PromptMode.NATURAL:
        return SYSTEM_PROMPT_NATURAL
    return SYSTEM_PROMPT_STRICT


def call_llm(
    backbone: Backbone,
    scenario_prompt: str,
    known_objects: List[str],
    temperature: float = 0.7,
    api_key: Optional[str] = None,
    client_factory: Optional[Any] = None,
    mode: PromptMode = PromptMode.NATURAL,
    object_positions: Optional[Dict[str, Any]] = None,
) -> LlmResponse:
    """OpenAI API 한 번 호출 → LlmResponse.

    Args:
        backbone: Backbone.GPT_4O 또는 Backbone.GPT_5_5
        scenario_prompt: 사용자 발화
        known_objects: 환경 안 객체 ID list
        temperature: sampling temperature (ADR-0025 D1.b 잠금 = 0.7)
        api_key: OpenAI API key. None 이면 OPENAI_API_KEY 환경변수
        client_factory: 테스트용 mock client factory. None 이면 openai.OpenAI()
        mode: PromptMode.NATURAL (calibration default) 또는 STRICT (paper §C 본실험)

    Returns:
        LlmResponse — action (None 가능, NATURAL 에서 function call 회피 시) + text_content

    Raises:
        ImportError: openai 패키지 미설치 (실 호출 시점)
        ValueError: API key 미설정
        RuntimeError: API 응답 형식 이상 (choices 없음 등)
    """
    if client_factory is None:
        try:
            import openai  # noqa: F401 — lazy import
        except ImportError as e:
            raise ImportError(
                'openai 패키지 필요 — `pip install -r requirements-calibration.txt`'
            ) from e
        resolved_key = api_key or os.environ.get('OPENAI_API_KEY')
        if not resolved_key:
            raise ValueError(
                'OPENAI_API_KEY 환경변수 또는 api_key 인자 필수'
            )
        client = openai.OpenAI(api_key=resolved_key)
    else:
        client = client_factory()

    system_prompt = _select_system_prompt(mode)
    user_msg = _build_user_message(scenario_prompt, known_objects, mode, object_positions)
    tool_choice = 'auto' if mode == PromptMode.NATURAL else 'required'

    import time
    t0 = time.monotonic()
    completion = client.chat.completions.create(
        model=resolve_model_id(backbone),
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_msg},
        ],
        tools=SKILL_FUNCTIONS,
        tool_choice=tool_choice,
        temperature=temperature,
    )
    latency_s = time.monotonic() - t0

    return _parse_completion(completion, latency_s)


def _parse_completion(completion: Any, latency_s: float) -> LlmResponse:
    """OpenAI completion 객체 → LlmResponse.

    NATURAL 모드에서 function call 없을 수 있음 — action=None + text_content 채움.
    STRICT 모드에서 function call 없으면 *오류* 인데, 본 함수는 *수동 검증* X —
    measure.py 측에서 strict + action=None 케이스 경고 처리.
    """
    if not completion.choices:
        raise RuntimeError('OpenAI 응답에 choices 없음 — API 측 이상')
    choice = completion.choices[0]
    tool_calls = getattr(choice.message, 'tool_calls', None) or []

    text_content = getattr(choice.message, 'content', None)

    if not tool_calls:
        # NATURAL 모드의 자연 거동 — function call 회피, prose 응답
        return LlmResponse(
            action=None,
            text_content=text_content,
            raw_response=None,
            cost_usd=0.0,
            latency_s=latency_s,
        )

    tc = tool_calls[0]
    import json
    sigma = tc.function.name
    theta = json.loads(tc.function.arguments) if tc.function.arguments else {}

    action = TypedAction(sigma=sigma, theta=theta)
    return LlmResponse(
        action=action,
        text_content=text_content,
        raw_response=None,
        cost_usd=0.0,
        latency_s=latency_s,
    )
