"""ADR-0013 D2 5 스킬 카탈로그 — paper-1 1차 시안 스킬 집합 $\\mathcal{A}$.

[ADR-0013 D2](../../../docs/handover/decisions/0013-tier2-spec-lock.md#d2) 측
5 스킬 source-of-truth. 모든 *의도해석기* wrapper 측 본 카탈로그 측 5 스킬
중 하나 출력 — closed vocabulary.

| 스킬 σ | 인자 도메인 $\\mathcal{D}_\\sigma$ | action_class |
|---|---|---|
| `move_to` | `position`: 지오펜스 안 $\\mathbb{R}^3$, `max_speed` ∈ [0, 0.5] m/s | monitoring |
| `inspect` | `target_id` ∈ known_objects, `viewpoint` ∈ {overview, close, top} | monitoring |
| `return_to_dock` | () | return |
| `emergency_land` | () | return |
| `ask_user` | `question`: str, `options`: list[str] | confirm |

action_class 측 ADR-0013 D3 시간논리 사양 $\\Phi_3$ (confirm 강제) · $\\Phi_{10}$
(명령 모순) 등 측 처분 분류.

`known_objects` 측 시나리오 SDF 측 정의 (S5·S6 거실 layout 기준). 후속 시나리오
(S3·S8 등) 측 확장.
"""

from __future__ import annotations

from enum import Enum
from typing import Tuple


class SkillName(str, Enum):
    """ADR-0013 D2 5 스킬 식별자."""

    MOVE_TO = 'move_to'
    INSPECT = 'inspect'
    RETURN_TO_DOCK = 'return_to_dock'
    EMERGENCY_LAND = 'emergency_land'
    ASK_USER = 'ask_user'


class ActionClass(str, Enum):
    """ADR-0013 D2 action_class 분류 — Tier 2 시간논리 사양 측 처분 입력."""

    MONITORING = 'monitoring'
    RETURN = 'return'
    CONFIRM = 'confirm'


# ADR-0013 D2 표 source-of-truth.
SKILL_ACTION_CLASS = {
    SkillName.MOVE_TO: ActionClass.MONITORING,
    SkillName.INSPECT: ActionClass.MONITORING,
    SkillName.RETURN_TO_DOCK: ActionClass.RETURN,
    SkillName.EMERGENCY_LAND: ActionClass.RETURN,
    SkillName.ASK_USER: ActionClass.CONFIRM,
}


# closed vocabulary — 5 스킬 외 출력 금지. classifier·LLM·VLA 모두 본 5 종 중
# 하나 산출.
ALL_SKILLS: Tuple[SkillName, ...] = (
    SkillName.MOVE_TO,
    SkillName.INSPECT,
    SkillName.RETURN_TO_DOCK,
    SkillName.EMERGENCY_LAND,
    SkillName.ASK_USER,
)


def action_class_of(skill: SkillName) -> ActionClass:
    """skill → action_class — ADR-0013 D2 매핑.

    Args:
        skill: SkillName.

    Returns:
        ActionClass.

    Raises:
        KeyError: 매핑 외 skill (defensive — 모든 SkillName 측 cover).
    """
    return SKILL_ACTION_CLASS[skill]
