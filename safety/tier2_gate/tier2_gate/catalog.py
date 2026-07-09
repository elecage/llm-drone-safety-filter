"""ADR-0013 D2 — 1차 스킬 카탈로그 + CC-1·CC-2 검증.

cmsm-proof §9.2 명령 계약 (Command Contract)의 코드화. 5 스킬 + 인자 도메인 +
action_class.  카탈로그·viewpoint 집합·max_speed 상한은 ADR-0013 D2와 동기.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ActionClass(str, Enum):
    """ADR-0013 D2 / cmsm-proof §9.3 Φ_2 — 스킬이 속한 동작 부류."""

    MONITORING = 'monitoring'
    RETURN = 'return'
    CONFIRM = 'confirm'


SKILL_ACTION_CLASS: Mapping[str, ActionClass] = {
    'move_to': ActionClass.MONITORING,
    'inspect': ActionClass.MONITORING,
    'return_to_dock': ActionClass.RETURN,
    'emergency_land': ActionClass.RETURN,
    'ask_user': ActionClass.CONFIRM,
}

CATALOG: frozenset[str] = frozenset(SKILL_ACTION_CLASS.keys())
"""CC-1 — 카탈로그 폐쇄성. paper-1 페르소나 = 정찰·모니터링·호출 카메라-only."""

INSPECT_VIEWPOINTS: frozenset[str] = frozenset({'overview', 'close', 'top'})
MOVE_TO_MAX_SPEED_HI: float = 0.5  # ADR-0013 D2: max_speed ∈ [0, 0.5] m/s.


@dataclass(frozen=True)
class Geofence:
    """AABB 지오펜스 — Φ_1 / move_to 인자 도메인의 위치 제약 입력.

    경계는 inclusive — `(xmin, ymin, zmin)`과 `(xmax, ymax, zmax)`도
    `contains(...) == True`. 시나리오 SDF의 wall/ceiling은 보통 두께가
    있으므로 inclusive 경계가 자연. 생성 시 `xmin ≤ xmax` 등 강제.
    """

    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float

    def __post_init__(self) -> None:
        assert self.xmin <= self.xmax, f'Geofence: xmin={self.xmin} > xmax={self.xmax}'
        assert self.ymin <= self.ymax, f'Geofence: ymin={self.ymin} > ymax={self.ymax}'
        assert self.zmin <= self.zmax, f'Geofence: zmin={self.zmin} > zmax={self.zmax}'

    def contains(self, position: tuple[float, float, float]) -> bool:
        """경계 inclusive 포함 검사."""
        x, y, z = position
        return (
            self.xmin <= x <= self.xmax
            and self.ymin <= y <= self.ymax
            and self.zmin <= z <= self.zmax
        )


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str = ''


def validate_command(
    sigma: str,
    theta: Mapping[str, Any],
    *,
    geofence: Geofence,
    known_objects: frozenset[str],
) -> ValidationResult:
    """cmsm-proof §9.2 / ADR-0013 D2 — CC-1 + CC-2 검증.

    valid=True iff σ ∈ CATALOG and θ가 σ별 도메인을 만족.
    """
    if sigma not in CATALOG:
        return ValidationResult(False, f'CC-1: unknown skill "{sigma}"')

    if sigma == 'move_to':
        return _validate_move_to(theta, geofence=geofence)
    if sigma == 'inspect':
        return _validate_inspect(theta, known_objects=known_objects)
    if sigma in ('return_to_dock', 'emergency_land'):
        return _validate_empty(theta, sigma=sigma)
    if sigma == 'ask_user':
        return _validate_ask_user(theta)
    raise AssertionError(f'카탈로그에 있으나 validator 없음: {sigma}')


def _validate_move_to(theta: Mapping[str, Any], *, geofence: Geofence) -> ValidationResult:
    pos = theta.get('position')
    if not isinstance(pos, (tuple, list)) or len(pos) != 3:
        return ValidationResult(False, 'CC-2: move_to.position must be 3-tuple')
    try:
        position = (float(pos[0]), float(pos[1]), float(pos[2]))
    except (TypeError, ValueError):
        return ValidationResult(False, 'CC-2: move_to.position not numeric')
    if not geofence.contains(position):
        return ValidationResult(
            False, f'CC-2: move_to.position {position} outside geofence'
        )

    max_speed = theta.get('max_speed')
    if isinstance(max_speed, bool) or not isinstance(max_speed, (int, float)):
        return ValidationResult(False, 'CC-2: move_to.max_speed must be number')
    if not 0.0 <= float(max_speed) <= MOVE_TO_MAX_SPEED_HI:
        return ValidationResult(
            False,
            f'CC-2: move_to.max_speed {max_speed} ∉ [0, {MOVE_TO_MAX_SPEED_HI}]',
        )
    return ValidationResult(True)


def _validate_inspect(
    theta: Mapping[str, Any], *, known_objects: frozenset[str]
) -> ValidationResult:
    target = theta.get('target_id')
    if not isinstance(target, str) or target not in known_objects:
        return ValidationResult(
            False, f'CC-2: inspect.target_id "{target}" ∉ known_objects'
        )
    viewpoint = theta.get('viewpoint')
    if viewpoint not in INSPECT_VIEWPOINTS:
        return ValidationResult(
            False,
            f'CC-2: inspect.viewpoint "{viewpoint}" ∉ {set(INSPECT_VIEWPOINTS)}',
        )
    return ValidationResult(True)


def _validate_empty(theta: Mapping[str, Any], *, sigma: str) -> ValidationResult:
    if len(theta) != 0:
        return ValidationResult(
            False, f'CC-2: {sigma}.theta must be empty (got keys {list(theta)})'
        )
    return ValidationResult(True)


def _validate_ask_user(theta: Mapping[str, Any]) -> ValidationResult:
    question = theta.get('question')
    if not isinstance(question, str) or not question.strip():
        return ValidationResult(False, 'CC-2: ask_user.question must be non-empty str')
    options = theta.get('options')
    if not isinstance(options, list) or any(not isinstance(o, str) for o in options):
        return ValidationResult(False, 'CC-2: ask_user.options must be list[str]')
    return ValidationResult(True)
