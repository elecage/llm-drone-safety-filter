"""intent_llm._llm_prompt 단위 테스트.

build_messages / parse_typed_action / compute_skill_entropy / majority_vote +
SYSTEM_PROMPT 측 SkillName 정합 검증 (C14 amendment).
"""

from __future__ import annotations

import json
import math
import os
from typing import List
from unittest import mock

import pytest

from intent_llm._llm_prompt import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_BASE,
    _VALID_SKILL_VALUES,
    build_messages,
    compute_skill_entropy,
    majority_vote,
    parse_typed_action,
)
from intent_llm.interface import TypedAction
from intent_llm.skill_catalog import SkillName


# ----------------------------------------------------------------- SYSTEM_PROMPT 정합


class TestSystemPromptIntegrity:
    """SYSTEM_PROMPT 측 SkillName 열거형 정합 — C14 amendment 핵심 검증."""

    def test_return_to_dock_in_prompt(self) -> None:
        assert 'return_to_dock' in SYSTEM_PROMPT

    def test_emergency_land_in_prompt(self) -> None:
        assert 'emergency_land' in SYSTEM_PROMPT

    def test_no_return_home_alias(self) -> None:
        # 구 오류 alias — SkillName 에 없으므로 prompt 에 있으면 안 됨.
        assert 'return_home' not in SYSTEM_PROMPT

    def test_no_land_alias(self) -> None:
        # 구 오류 alias — SkillName 에 없으므로 prompt 에 있으면 안 됨.
        assert '- land:' not in SYSTEM_PROMPT

    def test_valid_skill_values_match_skill_name_enum(self) -> None:
        expected = frozenset(s.value for s in SkillName)
        assert _VALID_SKILL_VALUES == expected

    def test_all_five_skills_in_prompt(self) -> None:
        for skill in SkillName:
            assert skill.value in SYSTEM_PROMPT, f'{skill.value!r} SYSTEM_PROMPT 누락'

    def test_move_to_args_format_in_prompt(self) -> None:
        # ADR-0027 amendment: move_to = target_id(객체명) | direction (좌표 직접 출력 폐기)
        assert '"target_id"' in SYSTEM_PROMPT
        assert '"direction"' in SYSTEM_PROMPT

    def test_no_position_description_in_prompt(self) -> None:
        # ADR-0027: 구 형식이 SYSTEM_PROMPT 에 남아 있으면 안 됨
        assert 'position_description' not in SYSTEM_PROMPT

    def test_prompt_forbids_raw_coordinates(self) -> None:
        # ADR-0027 amendment: LLM 은 좌표를 출력하지 말아야 함 (결정론 lookup 대체)
        assert 'Do NOT output coordinates' in SYSTEM_PROMPT


# ----------------------------------------------------------------- build_messages


class TestBuildMessages:
    def test_returns_two_messages(self) -> None:
        msgs = build_messages('go home', 'S3')
        assert len(msgs) == 2

    def test_first_message_is_system(self) -> None:
        # VOICE_LANG 미설정 → 'auto' 분기. SYSTEM_PROMPT (import-time, 'auto') 와 일치.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('VOICE_LANG', None)
            msgs = build_messages('go home', 'S3')
        assert msgs[0]['role'] == 'system'
        assert msgs[0]['content'] == SYSTEM_PROMPT

    def test_second_message_is_user(self) -> None:
        msgs = build_messages('go home', 'S3')
        assert msgs[1]['role'] == 'user'

    def test_utterance_in_user_content(self) -> None:
        msgs = build_messages('충전 독으로 복귀', 'S3')
        assert '충전 독으로 복귀' in msgs[1]['content']

    def test_scenario_id_in_user_content(self) -> None:
        msgs = build_messages('test', 'S5')
        assert 'S5' in msgs[1]['content']

    def test_context_graph_serialized_in_user_content(self) -> None:
        ctx = {'objects': ['sofa', 'mug']}
        msgs = build_messages('test', 'S3', context_graph=ctx)
        assert 'sofa' in msgs[1]['content']

    def test_no_context_graph_omits_context_key(self) -> None:
        msgs = build_messages('test', 'S3', context_graph=None)
        assert 'Context' not in msgs[1]['content']

    def test_empty_context_graph_omits_context_key(self) -> None:
        # 빈 dict → falsy → context 절 미포함.
        msgs = build_messages('test', 'S3', context_graph={})
        assert 'Context' not in msgs[1]['content']


# ----------------------------------------------------------------- VOICE_LANG 답변 언어 지시


class TestVoiceLangPromptDirective:
    """build_messages + parse_typed_action 측 VOICE_LANG 별 분기 (STT/TTS 정합)."""

    def test_ko_directive_in_system_prompt(self) -> None:
        with mock.patch.dict(os.environ, {'VOICE_LANG': 'ko'}):
            msgs = build_messages('충전 독으로', 'S5')
        content = msgs[0]['content']
        assert SYSTEM_PROMPT_BASE in content
        assert 'Korean' in content or '한국어' in content
        assert 'ask_user' in content

    def test_en_directive_in_system_prompt(self) -> None:
        with mock.patch.dict(os.environ, {'VOICE_LANG': 'en'}):
            msgs = build_messages('return to dock', 'S5')
        content = msgs[0]['content']
        assert SYSTEM_PROMPT_BASE in content
        assert 'English' in content

    def test_auto_directive_matches_user_language(self) -> None:
        with mock.patch.dict(os.environ, {'VOICE_LANG': 'auto'}):
            msgs = build_messages('test', 'S5')
        content = msgs[0]['content']
        # auto 측 "same language as the user command" 류 지시.
        assert 'same' in content.lower() and 'language' in content.lower()

    def test_unknown_value_falls_back_to_auto(self) -> None:
        with mock.patch.dict(os.environ, {'VOICE_LANG': 'ja'}):
            msgs = build_messages('test', 'S5')
        assert 'same' in msgs[0]['content'].lower()

    def test_fallback_question_ko(self) -> None:
        with mock.patch.dict(os.environ, {'VOICE_LANG': 'ko'}):
            ta = parse_typed_action('not json')
        assert ta.skill == SkillName.ASK_USER
        # 한국어 fallback 질문 — 한글 포함.
        q = ta.args['question']
        assert any('가' <= ch <= '힣' for ch in q), f'한글 미포함: {q!r}'

    def test_fallback_question_en(self) -> None:
        with mock.patch.dict(os.environ, {'VOICE_LANG': 'en'}):
            ta = parse_typed_action('not json')
        assert ta.skill == SkillName.ASK_USER
        q = ta.args['question']
        # 영어 fallback — ASCII only.
        assert q.isascii(), f'영어 아님: {q!r}'

    def test_fallback_question_auto_defaults_to_english(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('VOICE_LANG', None)
            ta = parse_typed_action('not json')
        assert ta.args['question'].isascii()


# ----------------------------------------------------------------- parse_typed_action


class TestParseTypedAction:
    def _json(self, skill: str, args: dict | None = None) -> str:
        return json.dumps({'skill': skill, 'args': args or {}})

    def test_move_to(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'position': [-1.8, -1.5, 1.015]}))
        assert ta.skill == SkillName.MOVE_TO

    def test_move_to_position_description_fallback(self) -> None:
        # 구 형식 position_description → position 검증 실패 → ASK_USER (ADR-0027)
        ta = parse_typed_action(self._json('move_to', {'position_description': 'TV'}))
        assert ta.skill == SkillName.ASK_USER

    def test_move_to_missing_position_fallback(self) -> None:
        ta = parse_typed_action(self._json('move_to', {}))
        assert ta.skill == SkillName.ASK_USER

    def test_move_to_position_wrong_length_fallback(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'position': [1.0, 2.0]}))
        assert ta.skill == SkillName.ASK_USER

    def test_move_to_position_non_numeric_fallback(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'position': ['a', 'b', 'c']}))
        assert ta.skill == SkillName.ASK_USER

    def test_move_to_position_preserved(self) -> None:
        pos = [1.0, -2.5, 0.8]
        ta = parse_typed_action(self._json('move_to', {'position': pos}))
        assert ta.args['position'] == pos

    # ADR-0027 amendment: target_id(객체명) / direction 토큰.
    def test_move_to_target_id(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'target_id': 'sofa'}))
        assert ta.skill == SkillName.MOVE_TO
        assert ta.args['target_id'] == 'sofa'

    def test_move_to_direction(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'direction': 'forward'}))
        assert ta.skill == SkillName.MOVE_TO
        assert ta.args['direction'] == 'forward'

    def test_move_to_direction_case_insensitive(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'direction': 'LEFT'}))
        assert ta.skill == SkillName.MOVE_TO

    def test_move_to_invalid_direction_fallback(self) -> None:
        # 허용 토큰 외 방향 → ASK_USER.
        ta = parse_typed_action(self._json('move_to', {'direction': 'northeast'}))
        assert ta.skill == SkillName.ASK_USER

    def test_move_to_empty_target_id_fallback(self) -> None:
        ta = parse_typed_action(self._json('move_to', {'target_id': '  '}))
        assert ta.skill == SkillName.ASK_USER

    def test_inspect(self) -> None:
        ta = parse_typed_action(self._json('inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}))
        assert ta.skill == SkillName.INSPECT

    def test_return_to_dock(self) -> None:
        ta = parse_typed_action(self._json('return_to_dock'))
        assert ta.skill == SkillName.RETURN_TO_DOCK

    def test_emergency_land(self) -> None:
        ta = parse_typed_action(self._json('emergency_land'))
        assert ta.skill == SkillName.EMERGENCY_LAND

    def test_ask_user(self) -> None:
        ta = parse_typed_action(self._json('ask_user', {'question': '어디로 갈까요?'}))
        assert ta.skill == SkillName.ASK_USER

    def test_wrong_skill_name_fallback(self) -> None:
        # 구 오류명 — SkillName 에 없으므로 ASK_USER fallback.
        ta = parse_typed_action(self._json('return_home'))
        assert ta.skill == SkillName.ASK_USER

    def test_wrong_land_alias_fallback(self) -> None:
        ta = parse_typed_action(self._json('land'))
        assert ta.skill == SkillName.ASK_USER

    def test_invalid_json_fallback(self) -> None:
        ta = parse_typed_action('not json at all')
        assert ta.skill == SkillName.ASK_USER

    def test_empty_string_fallback(self) -> None:
        ta = parse_typed_action('')
        assert ta.skill == SkillName.ASK_USER

    def test_missing_skill_key_fallback(self) -> None:
        ta = parse_typed_action(json.dumps({'args': {}}))
        assert ta.skill == SkillName.ASK_USER

    def test_args_preserved(self) -> None:
        args = {'target_id': 'mug_left', 'viewpoint': 'close'}
        ta = parse_typed_action(self._json('inspect', args))
        assert ta.args == args

    def test_non_dict_args_normalized(self) -> None:
        # move_to 측 non-dict args → {} 정규화 → position 검증 실패 → ASK_USER (ADR-0027)
        raw = json.dumps({'skill': 'move_to', 'args': 'bad'})
        ta = parse_typed_action(raw)
        assert ta.skill == SkillName.ASK_USER

    def test_uppercase_skill_accepted(self) -> None:
        # parse_typed_action 은 .lower() 정규화 — 대문자 입력도 유효 skill 로 수락.
        ta = parse_typed_action(self._json('MOVE_TO', {'position': [0.0, 1.0, 1.5]}))
        assert ta.skill == SkillName.MOVE_TO

    def test_whitespace_stripped(self) -> None:
        raw = '  ' + self._json('return_to_dock') + '  '
        ta = parse_typed_action(raw)
        assert ta.skill == SkillName.RETURN_TO_DOCK


# ----------------------------------------------------------------- compute_skill_entropy


class TestComputeSkillEntropy:
    def test_empty_list_returns_one(self) -> None:
        assert compute_skill_entropy([]) == 1.0

    def test_all_same_returns_zero(self) -> None:
        skills = [SkillName.MOVE_TO] * 5
        assert compute_skill_entropy(skills) == pytest.approx(0.0)

    def test_two_distinct_skills_returns_positive(self) -> None:
        skills = [SkillName.MOVE_TO, SkillName.INSPECT]
        h = compute_skill_entropy(skills)
        assert 0.0 < h <= 1.0

    def test_all_five_distinct_returns_one(self) -> None:
        skills = list(SkillName)
        h = compute_skill_entropy(skills)
        assert h == pytest.approx(1.0)

    def test_result_in_unit_interval(self) -> None:
        skills = [SkillName.MOVE_TO, SkillName.INSPECT, SkillName.ASK_USER]
        h = compute_skill_entropy(skills)
        assert 0.0 <= h <= 1.0

    def test_single_element_is_zero(self) -> None:
        assert compute_skill_entropy([SkillName.RETURN_TO_DOCK]) == pytest.approx(0.0)


# ----------------------------------------------------------------- majority_vote


class TestMajorityVote:
    def test_empty_list_returns_ask_user_zero(self) -> None:
        skill, rho = majority_vote([])
        assert skill == SkillName.ASK_USER
        assert rho == 0.0

    def test_unanimous_returns_full_rho(self) -> None:
        skills = [SkillName.MOVE_TO, SkillName.MOVE_TO, SkillName.MOVE_TO]
        skill, rho = majority_vote(skills)
        assert skill == SkillName.MOVE_TO
        assert rho == pytest.approx(1.0)

    def test_majority_two_of_three(self) -> None:
        skills = [SkillName.MOVE_TO, SkillName.MOVE_TO, SkillName.INSPECT]
        skill, rho = majority_vote(skills)
        assert skill == SkillName.MOVE_TO
        assert rho == pytest.approx(2 / 3)

    def test_single_element(self) -> None:
        skill, rho = majority_vote([SkillName.RETURN_TO_DOCK])
        assert skill == SkillName.RETURN_TO_DOCK
        assert rho == pytest.approx(1.0)

    def test_rho_in_unit_interval(self) -> None:
        skills = [SkillName.MOVE_TO, SkillName.INSPECT, SkillName.ASK_USER]
        _, rho = majority_vote(skills)
        assert 0.0 <= rho <= 1.0
