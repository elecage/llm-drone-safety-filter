"""Cloud/Edge LLM wrapper 공통 prompt 빌더 + response 파서 + 신호 계산.

C14 — 실 API wiring 측 공유 logic. cloud_llm.py + edge_llm.py 측 import.

## 설계 원칙

- `build_messages()` 측 OpenAI / Ollama 양쪽 동일 messages list 포맷 사용.
- `parse_typed_action()` 측 JSON 파싱 실패 시 ASK_USER fallback — wrapper 측
  *항상 유효 IntentResult 산출* 계약 ([interface.py](interface.py)) 정합.
- `compute_skill_entropy()` + `majority_vote()` 측 M회 독립 호출 집계.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from typing import Dict, List, Optional, Tuple

from intent_llm.skill_catalog import SkillName


# 드론 의도 파싱 시스템 프롬프트.
# ADR-0027: move_to.position = [x, y, z] (world frame, m). Context 객체 좌표 직접 출력.
# 순수 방향 명령(left/forward 등)도 절대 좌표만 출력 — drone_position(있을 때) 기준
# 으로 LLM 이 직접 계산. args 스키마는 항상 절대 position [x,y,z] 유지.
SYSTEM_PROMPT_BASE: str = """\
You are an intent parsing assistant for an assistive drone system supporting a user with quadriplegia.
Parse the spoken command and return a JSON object for the intended drone action.

CAPABILITIES (hard boundary): this is a camera-only reconnaissance/monitoring drone.
It can ONLY fly to a location (move_to) or observe an object/area with its camera
(inspect). It CANNOT pick up, carry, fetch, deliver, hand over, or physically move
any object. If the command asks for something outside these two capabilities
(e.g. "bring me X", "가져와", "fetch X", "grab X"), do NOT phrase the reply as if you
could do it. Use ask_user to briefly note that you can only move or look, and ask
where to go or what to inspect (e.g. "물건을 옮길 수는 없어요. 어디로 갈까요, 아니면
무엇을 살펴볼까요?"). Never ask "what should I bring" or similar.

When a Context is provided it may include:
  - "objects": named objects with their 3D positions [x, y, z] in metres (world ENU frame).
  - "user_position": the user's [x, y, z] in metres (world ENU).
  - "drone_position" (optional): the drone's current [x, y, z] in metres (world ENU).
All coordinates are world ENU: +X=East, +Y=North, +Z=Up.

Available skills:
- move_to: Move the drone to a location. Do NOT output coordinates — the system
  resolves the exact position deterministically. Output EITHER a named target OR a
  relative direction:
  Args: {"target_id": "<object_name>"}   -- name copied from Context "objects"
     or {"direction": "forward"|"back"|"left"|"right"|"up"|"down"}  -- relative move
  Resolution rules (try in this order):
    1. If the command refers to an object listed in Context "objects", output
       {"target_id": "<that object's exact name>"}. Copy the name string verbatim
       from the Context list (e.g. Korean "소파" → the object named "sofa", "식탁" →
       "dining_table"). Never invent or compute coordinates.
    2. If the command gives only a relative direction (e.g. "앞으로", "왼쪽",
       "forward", "left"), output {"direction": "<one of forward/back/left/right/up/down>"}.
    3. Otherwise (no matching object and no clear direction), use ask_user.
- inspect: Inspect an object or area.
  Args: {"target_id": "<object_name>", "viewpoint": "overview"|"close"|"top"}
  Use an object name from the Context list. If unclear, use ask_user.
- return_to_dock: Return the drone to its charging dock. Args: {}
- emergency_land: Land the drone immediately. Args: {}
- ask_user: Ask the user to clarify. Args: {"question": "<clarifying question>"}

Output ONLY a JSON object with exactly this format:
{"skill": "<skill_name>", "args": {<args_dict>}}

If the command is unclear, ambiguous, or none of the move_to resolution rules apply, use ask_user.\
"""

# VOICE_LANG (STT·TTS 공통) → ask_user.question 작성 언어 지시 + parse-fallback 질문.
# wrapper_node 측 docker exec 환경 (start_intent_stack.sh 가 forward) 에서 읽힘.
# 'auto' = 모델이 사용자 명령 언어와 동일 언어로 답변 (한국어 발화→한국어 질문 등).
_LANGUAGE_DIRECTIVES: Dict[str, str] = {
    'ko': (
        'Write the value of "question" inside ask_user in natural Korean (한국어). '
        'Do not mix English unless the user spoke English. '
        'Other JSON keys (skill, args, position, ...) stay in English.'
    ),
    'en': (
        'Write the value of "question" inside ask_user in natural English. '
        'Other JSON keys stay in English.'
    ),
    'auto': (
        'Write the value of "question" inside ask_user in the same natural language '
        'as the user command (e.g., Korean command → Korean question, English → English). '
        'Other JSON keys (skill, args, position, ...) stay in English.'
    ),
}

_FALLBACK_QUESTION: Dict[str, str] = {
    'ko': '죄송해요, 다시 한 번 말씀해 주시겠어요?',
    'en': 'Could you please clarify your request?',
}


def _voice_lang() -> str:
    """현재 VOICE_LANG (ko/en/auto). 알 수 없는 값은 'auto'."""
    raw = (os.environ.get('VOICE_LANG') or 'auto').strip().lower()
    return raw if raw in ('ko', 'en', 'auto') else 'auto'


def _system_prompt(voice_lang: str) -> str:
    """SYSTEM_PROMPT_BASE + VOICE_LANG 기반 답변 언어 지시."""
    directive = _LANGUAGE_DIRECTIVES.get(voice_lang, _LANGUAGE_DIRECTIVES['auto'])
    return f'{SYSTEM_PROMPT_BASE}\n\n{directive}'


def _fallback_question(voice_lang: str) -> str:
    """parse 실패 시 ask_user 질문 — VOICE_LANG 별 (auto 는 영어)."""
    return _FALLBACK_QUESTION.get(voice_lang, _FALLBACK_QUESTION['en'])


# 호환 — 기존 SYSTEM_PROMPT 참조하는 곳이 있을 수 있어 module-load 시 한 번 빌드.
SYSTEM_PROMPT: str = _system_prompt(_voice_lang())

_VALID_SKILL_VALUES: frozenset = frozenset(s.value for s in SkillName)


_VALID_DIRECTIONS: frozenset = frozenset(
    {'forward', 'back', 'left', 'right', 'up', 'down'}
)


def _valid_position(args: Dict) -> bool:
    """move_to.args 측 position [x, y, z] 3개 숫자 검증 (레거시, ADR-0027 D2 이전)."""
    pos = args.get('position')
    if pos is None:
        return False
    try:
        if len(pos) != 3:
            return False
        for v in pos:
            float(v)
        return True
    except (TypeError, ValueError):
        return False


def _valid_move_to(args: Dict) -> bool:
    """move_to.args 검증 (ADR-0027 amendment).

    유효 = (a) target_id 비어있지 않은 문자열 (객체명), 또는 (b) direction 이
    허용 토큰, 또는 (c) 레거시 position [x,y,z]. 셋 다 아니면 무효 → ask_user.
    LLM 좌표 직접 출력을 폐기하고 의미 선택(target_id/direction)으로 대체 —
    좌표 산출은 sigma_bridge 결정론 lookup 담당.
    """
    tid = args.get('target_id') or args.get('target')
    if isinstance(tid, str) and tid.strip():
        return True
    d = args.get('direction')
    if isinstance(d, str) and d.strip().lower() in _VALID_DIRECTIONS:
        return True
    return _valid_position(args)


def build_messages(
    utterance: str,
    scenario_id: str,
    context_graph: Optional[Dict] = None,
) -> List[Dict]:
    """Utterance + scenario + (optional) context → OpenAI/Ollama messages list.

    System prompt 는 VOICE_LANG (ko/en/auto) 별 답변 언어 지시를 포함한다.
    매 호출마다 env 를 다시 읽어 런타임 toggle 가능 (테스트 용이).
    """
    parts = [f'Scenario: {scenario_id}', f'Command: "{utterance}"']
    if context_graph:
        parts.append(f'Context: {json.dumps(context_graph, ensure_ascii=False)}')
    return [
        {'role': 'system', 'content': _system_prompt(_voice_lang())},
        {'role': 'user', 'content': '\n'.join(parts)},
    ]


def parse_typed_action(response_text: str):
    """JSON response text → TypedAction.

    JSON 파싱 오류 / 알 수 없는 skill / 빈 응답 → ASK_USER fallback
    (질문 텍스트는 VOICE_LANG 별 — 한국어/영어).
    """
    from intent_llm.interface import TypedAction

    fallback = {'question': _fallback_question(_voice_lang())}
    try:
        data = json.loads(response_text.strip())
        skill_str = str(data.get('skill', '')).strip().lower()
        if skill_str not in _VALID_SKILL_VALUES:
            raise ValueError(f'unknown skill: {skill_str!r}')
        skill = SkillName(skill_str)
        args = data.get('args', {})
        if not isinstance(args, dict):
            args = {}
        # ADR-0027 amendment: move_to = target_id(객체명) | direction | (레거시)position.
        if skill == SkillName.MOVE_TO and not _valid_move_to(args):
            return TypedAction(skill=SkillName.ASK_USER, args=fallback)
        return TypedAction(skill=skill, args=args)
    except Exception:
        from intent_llm.interface import TypedAction
        return TypedAction(skill=SkillName.ASK_USER, args=fallback)


def compute_skill_entropy(skills: List[SkillName]) -> float:
    """M회 skill 예측 측 정규화 Shannon 엔트로피 H ∈ [0, 1].

    0 = 모든 호출 동일 skill (완전 일치). 1 = 균일 분포 (최대 불확실).

    주의 — 이는 *skill 분포* 엔트로피로, 정본 s1(접지 엔트로피 = OVD referent
    점수 분포 H, cmsm-proof §2.1)이 *아니다*. wrapper signals 의 정본 신호에는
    포함하지 않으며, trial log 진단용으로만 산출한다.
    """
    n = len(skills)
    if n == 0:
        return 1.0
    counts = Counter(skills)
    raw_h = -sum((c / n) * math.log(c / n) for c in counts.values() if c > 0)
    n_skills = len(SkillName)
    max_h = math.log(n_skills) if n_skills > 1 else 1.0
    return min(1.0, raw_h / max_h) if max_h > 0.0 else 0.0


def majority_vote(skills: List[SkillName]) -> Tuple[SkillName, float]:
    """(majority_skill, ρ = majority_count / M).

    동점 시 Counter.most_common 측 첫 번째 (삽입 순서 기준).
    빈 list → (ASK_USER, 0.0).
    """
    if not skills:
        return SkillName.ASK_USER, 0.0
    counts = Counter(skills)
    best_skill, best_count = counts.most_common(1)[0]
    rho = best_count / len(skills)
    return best_skill, rho
