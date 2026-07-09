"""llm_client.py 단위 테스트 — mock client 로 host venv 통과.

실 OpenAI API 호출 없이 client_factory 주입으로 검증.

PR #82 review C1·C2·C4 amendment 측 테스트 추가:
- PromptMode.NATURAL vs STRICT 분기
- SYSTEM_PROMPT_NATURAL / STRICT 두 모드
- resolve_model_id 의 GPT-5.5 env var override (C4)
- NATURAL 모드의 action=None 자연 거동
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, List, Optional
from unittest import mock

import pytest

from eval_calibration.llm_client import (
    SKILL_FUNCTIONS,
    SYSTEM_PROMPT_NATURAL,
    SYSTEM_PROMPT_STRICT,
    LlmResponse,
    PromptMode,
    call_llm,
    resolve_model_id,
)
from eval_calibration.schemas import Backbone


@dataclass
class _MockFunction:
    name: str
    arguments: str


@dataclass
class _MockToolCall:
    function: _MockFunction


@dataclass
class _MockMessage:
    tool_calls: List[_MockToolCall]
    content: Optional[str] = None


@dataclass
class _MockChoice:
    message: _MockMessage


@dataclass
class _MockCompletion:
    choices: List[_MockChoice]


class _MockCompletions:
    def __init__(self, completion: _MockCompletion):
        self._completion = completion
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._completion


class _MockChatNamespace:
    def __init__(self, completions: _MockCompletions):
        self.completions = completions


class _MockOpenAIClient:
    def __init__(self, completion: _MockCompletion):
        self.chat = _MockChatNamespace(_MockCompletions(completion))


def _make_tool_call_completion(name: str, arguments: dict) -> _MockCompletion:
    return _MockCompletion(
        choices=[
            _MockChoice(
                message=_MockMessage(
                    tool_calls=[_MockToolCall(function=_MockFunction(name=name, arguments=json.dumps(arguments)))],
                    content=None,
                )
            )
        ]
    )


def _make_no_call_completion(text: str = '') -> _MockCompletion:
    """NATURAL 모드의 자연 fail-gracefully — function call 없이 prose 응답."""
    return _MockCompletion(
        choices=[_MockChoice(message=_MockMessage(tool_calls=[], content=text))]
    )


class TestSkillFunctions:
    def test_five_skills_in_catalog(self):
        names = {f['function']['name'] for f in SKILL_FUNCTIONS}
        assert names == {'move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'}

    def test_move_to_requires_position_and_max_speed(self):
        move = next(f for f in SKILL_FUNCTIONS if f['function']['name'] == 'move_to')
        required = move['function']['parameters']['required']
        assert 'position' in required and 'max_speed' in required

    def test_ask_user_requires_question(self):
        ask = next(f for f in SKILL_FUNCTIONS if f['function']['name'] == 'ask_user')
        assert ask['function']['parameters']['required'] == ['question']


class TestSystemPrompts:
    def test_natural_minimal_no_enforce(self):
        # C2 amendment: NATURAL 은 "반드시 function call" 같은 제약 없음
        assert '반드시' not in SYSTEM_PROMPT_NATURAL
        assert 'known_objects' not in SYSTEM_PROMPT_NATURAL
        assert 'ADR-0013' not in SYSTEM_PROMPT_NATURAL

    def test_strict_has_catalog_enforcement(self):
        # STRICT 는 paper §C 본실험 측 — catalog 강제
        assert '반드시' in SYSTEM_PROMPT_STRICT
        assert 'known_objects' in SYSTEM_PROMPT_STRICT
        for sigma in ('move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'):
            assert sigma in SYSTEM_PROMPT_STRICT


class TestResolveModelId:
    """C4 amendment — Backbone enum → model ID with env var fallback."""

    def test_gpt_4o_is_stable(self):
        assert resolve_model_id(Backbone.GPT_4O) == 'gpt-4o-2024-05-13'

    def test_gpt_5_5_default(self, monkeypatch):
        monkeypatch.delenv('OPENAI_GPT_5_5_MODEL', raising=False)
        assert resolve_model_id(Backbone.GPT_5_5) == 'gpt-5.5'

    def test_gpt_5_5_env_override(self, monkeypatch):
        monkeypatch.setenv('OPENAI_GPT_5_5_MODEL', 'gpt-5-2026-04-23')
        assert resolve_model_id(Backbone.GPT_5_5) == 'gpt-5-2026-04-23'

    def test_gpt_4o_unaffected_by_env(self, monkeypatch):
        monkeypatch.setenv('OPENAI_GPT_5_5_MODEL', 'some-model')
        assert resolve_model_id(Backbone.GPT_4O) == 'gpt-4o-2024-05-13'


class TestCallLlmNaturalMode:
    """C1 amendment — NATURAL 모드 의 자연 거동 + tool_choice='auto'."""

    def test_natural_uses_auto_tool_choice(self):
        completion = _make_tool_call_completion('ask_user', {'question': 'q'})
        client = _MockOpenAIClient(completion)
        call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='x',
            known_objects=[],
            client_factory=lambda: client,
            mode=PromptMode.NATURAL,
        )
        assert client.chat.completions.last_kwargs['tool_choice'] == 'auto'

    def test_natural_uses_natural_system_prompt(self):
        completion = _make_tool_call_completion('ask_user', {'question': 'q'})
        client = _MockOpenAIClient(completion)
        call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='x',
            known_objects=[],
            client_factory=lambda: client,
            mode=PromptMode.NATURAL,
        )
        sys_msg = client.chat.completions.last_kwargs['messages'][0]
        assert sys_msg['content'] == SYSTEM_PROMPT_NATURAL

    def test_natural_no_call_returns_action_none(self):
        completion = _make_no_call_completion(text='죄송하지만 환경에 컵이 보이지 않아요.')
        client = _MockOpenAIClient(completion)
        response = call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='테이블 위 컵',
            known_objects=[],
            client_factory=lambda: client,
            mode=PromptMode.NATURAL,
        )
        # NATURAL 에서 function call 회피 → action=None + text_content 채움
        assert response.action is None
        assert '컵이 보이지 않' in response.text_content


class TestCallLlmStrictMode:
    """STRICT 모드 — paper §C 본실험 측 catalog 강제."""

    def test_strict_uses_required_tool_choice(self):
        completion = _make_tool_call_completion('move_to', {'position': [0, 0, 1], 'max_speed': 0.5})
        client = _MockOpenAIClient(completion)
        call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='x',
            known_objects=[],
            client_factory=lambda: client,
            mode=PromptMode.STRICT,
        )
        assert client.chat.completions.last_kwargs['tool_choice'] == 'required'

    def test_strict_uses_strict_system_prompt(self):
        completion = _make_tool_call_completion('move_to', {'position': [0, 0, 1], 'max_speed': 0.5})
        client = _MockOpenAIClient(completion)
        call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='x',
            known_objects=[],
            client_factory=lambda: client,
            mode=PromptMode.STRICT,
        )
        sys_msg = client.chat.completions.last_kwargs['messages'][0]
        assert sys_msg['content'] == SYSTEM_PROMPT_STRICT


class TestCallLlmParsing:
    """공통 parsing 로직."""

    def test_ask_user_response_parsed(self):
        completion = _make_tool_call_completion('ask_user', {'question': '어떤 컵?'})
        client = _MockOpenAIClient(completion)
        response = call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='테이블 위 컵',
            known_objects=['cup_on_table'],
            client_factory=lambda: client,
        )
        assert response.action is not None
        assert response.action.sigma == 'ask_user'
        assert response.action.theta['question'] == '어떤 컵?'

    def test_move_to_response_parsed(self):
        completion = _make_tool_call_completion(
            'move_to', {'position': [1.0, 2.0, 1.5], 'max_speed': 0.5}
        )
        client = _MockOpenAIClient(completion)
        response = call_llm(
            backbone=Backbone.GPT_5_5,
            scenario_prompt='저쪽으로',
            known_objects=[],
            client_factory=lambda: client,
        )
        assert response.action.sigma == 'move_to'
        assert response.action.theta['position'] == [1.0, 2.0, 1.5]

    def test_no_choices_raises(self):
        completion = _MockCompletion(choices=[])
        client = _MockOpenAIClient(completion)
        with pytest.raises(RuntimeError, match='choices 없음'):
            call_llm(
                backbone=Backbone.GPT_4O,
                scenario_prompt='x',
                known_objects=[],
                client_factory=lambda: client,
            )

    def test_kwargs_include_temperature_and_model(self):
        completion = _make_tool_call_completion('ask_user', {'question': 'q'})
        client = _MockOpenAIClient(completion)
        call_llm(
            backbone=Backbone.GPT_4O,
            scenario_prompt='x',
            known_objects=[],
            temperature=0.3,
            client_factory=lambda: client,
        )
        kwargs = client.chat.completions.last_kwargs
        assert kwargs['model'] == 'gpt-4o-2024-05-13'
        assert kwargs['temperature'] == 0.3
        assert len(kwargs['messages']) == 2

    def test_gpt_5_5_env_override_propagates_to_model_param(self, monkeypatch):
        # C4 amendment 의 end-to-end — env var → call_llm 의 model param
        monkeypatch.setenv('OPENAI_GPT_5_5_MODEL', 'gpt-5-future-2026')
        completion = _make_tool_call_completion('ask_user', {'question': 'q'})
        client = _MockOpenAIClient(completion)
        call_llm(
            backbone=Backbone.GPT_5_5,
            scenario_prompt='x',
            known_objects=[],
            client_factory=lambda: client,
        )
        assert client.chat.completions.last_kwargs['model'] == 'gpt-5-future-2026'


class TestBuildUserMessagePositions:
    """ADR-0025 amend 12 — context-provided 좌표 노출."""

    def test_with_positions_includes_coords(self) -> None:
        from eval_calibration.llm_client import _build_user_message, PromptMode
        msg = _build_user_message(
            '가줘', ['tv'], PromptMode.NATURAL, {'tv': (1.0, 2.0, 3.0)}
        )
        assert 'tv [1.000, 2.000, 3.000]' in msg

    def test_without_positions_names_only(self) -> None:
        from eval_calibration.llm_client import _build_user_message, PromptMode
        msg = _build_user_message('가줘', ['tv'], PromptMode.NATURAL)
        assert 'tv' in msg
        assert '[1.000' not in msg

    def test_partial_positions_falls_back_to_name(self) -> None:
        from eval_calibration.llm_client import _build_user_message, PromptMode
        msg = _build_user_message(
            '가줘', ['tv', 'sofa'], PromptMode.NATURAL, {'tv': (1.0, 2.0, 3.0)}
        )
        assert 'tv [1.000, 2.000, 3.000]' in msg
        assert 'sofa' in msg  # 좌표 없는 객체는 이름만
