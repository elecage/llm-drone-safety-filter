"""intent_llm.skill_catalog 단위 테스트.

ADR-0013 D2 5 스킬 + action_class 매핑 source-of-truth 잠금.
"""

from __future__ import annotations

import pytest

from intent_llm.skill_catalog import (
    ALL_SKILLS,
    SKILL_ACTION_CLASS,
    ActionClass,
    SkillName,
    action_class_of,
)


class TestSkillName:
    def test_five_skills(self) -> None:
        """ADR-0013 D2 = 5 스킬."""
        assert len(SkillName) == 5

    def test_skill_values(self) -> None:
        """스킬 식별자 string value 정합."""
        assert SkillName.MOVE_TO.value == 'move_to'
        assert SkillName.INSPECT.value == 'inspect'
        assert SkillName.RETURN_TO_DOCK.value == 'return_to_dock'
        assert SkillName.EMERGENCY_LAND.value == 'emergency_land'
        assert SkillName.ASK_USER.value == 'ask_user'

    def test_all_skills_covers_enum(self) -> None:
        """ALL_SKILLS tuple 측 SkillName enum 완전 cover."""
        assert set(ALL_SKILLS) == set(SkillName)
        assert len(ALL_SKILLS) == 5


class TestActionClass:
    def test_three_classes(self) -> None:
        """ADR-0013 D2 = 3 action_class."""
        assert len(ActionClass) == 3

    def test_class_values(self) -> None:
        assert ActionClass.MONITORING.value == 'monitoring'
        assert ActionClass.RETURN.value == 'return'
        assert ActionClass.CONFIRM.value == 'confirm'


class TestSkillActionClassMapping:
    """ADR-0013 D2 표 source-of-truth."""

    def test_move_to_is_monitoring(self) -> None:
        assert action_class_of(SkillName.MOVE_TO) == ActionClass.MONITORING

    def test_inspect_is_monitoring(self) -> None:
        assert action_class_of(SkillName.INSPECT) == ActionClass.MONITORING

    def test_return_to_dock_is_return(self) -> None:
        assert action_class_of(SkillName.RETURN_TO_DOCK) == ActionClass.RETURN

    def test_emergency_land_is_return(self) -> None:
        assert action_class_of(SkillName.EMERGENCY_LAND) == ActionClass.RETURN

    def test_ask_user_is_confirm(self) -> None:
        assert action_class_of(SkillName.ASK_USER) == ActionClass.CONFIRM

    def test_all_skills_mapped(self) -> None:
        """모든 SkillName 측 SKILL_ACTION_CLASS 측 cover — 정의되지 않은 매핑
        부재.
        """
        for skill in SkillName:
            assert skill in SKILL_ACTION_CLASS
            assert isinstance(SKILL_ACTION_CLASS[skill], ActionClass)

    def test_mapping_uses_all_action_classes(self) -> None:
        """3 action_class 측 모두 활용 — 미사용 class 부재."""
        used_classes = set(SKILL_ACTION_CLASS.values())
        assert used_classes == set(ActionClass)
