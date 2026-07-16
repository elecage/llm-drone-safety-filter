"""ADR-0013 D2 — 1차 스킬 카탈로그 + CC-1·CC-2 검증.

cmsm-proof §9.2 명령 계약 (Command Contract)의 코드화. 5 스킬 + 인자 도메인 +
action_class.  카탈로그·viewpoint 집합은 ADR-0013 D2와 동기.
``move_to``는 ADR-0049 D1로 의미 인자 계약(target_id | direction)으로 개정 —
종전 position 3-튜플·max_speed 계약(ADR-0013 D2 원본)은 폐기.
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
MOVE_TO_DIRECTIONS: frozenset[str] = frozenset(
    {'forward', 'back', 'left', 'right', 'up', 'down'}
)
"""ADR-0049 D1 — sigma_bridge `_DIRECTION_OFFSETS`·ESM Table S4와 동기."""


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
        return _validate_move_to(theta, known_objects=known_objects)
    if sigma == 'inspect':
        return _validate_inspect(theta, known_objects=known_objects)
    if sigma in ('return_to_dock', 'emergency_land'):
        return _validate_empty(theta, sigma=sigma)
    if sigma == 'ask_user':
        return _validate_ask_user(theta)
    raise AssertionError(f'카탈로그에 있으나 validator 없음: {sigma}')


def _validate_move_to(
    theta: Mapping[str, Any], *, known_objects: frozenset[str]
) -> ValidationResult:
    """ADR-0049 D1 — 의미 인자 계약 (ADR-0027 D9 출력 스키마와 동기).

    좌표(``position``)는 게이트 스키마 밖 — 좌표 산출은 게이트 하류의
    의도-제어 변환 모듈(sigma_bridge) 결정론 lookup 담당이라 게이트 통과
    시점엔 좌표가 존재하지 않는다. 좌표 환각·주입은 스키마에서 표현 자체가
    안 되는 구조로 차단. ``target_class`` 등 그 외 키는 parser 부산물로
    검증 없이 무시(다운스트림도 무시 — ask_user options 아티팩트 재발 방지).
    속도 한계는 티어 1(변화율 제한·CBF-QP)·티어 0(PX4 클램프) 담당,
    해소 좌표의 지오펜스는 운용 가드·티어 0 담당(ADR-0049 D3).
    """
    if 'position' in theta:
        return ValidationResult(
            False,
            'CC-2: move_to.position is outside the semantic contract '
            '(coordinates are resolved downstream; ADR-0049 D1)',
        )
    target = theta.get('target_id')
    direction = theta.get('direction')
    if (target is None) == (direction is None):
        return ValidationResult(
            False,
            'CC-2: move_to requires exactly one of target_id | direction '
            f'(got keys {sorted(theta)})',
        )
    if target is not None:
        if not isinstance(target, str) or target not in known_objects:
            return ValidationResult(
                False, f'CC-2: move_to.target_id "{target}" ∉ known_objects'
            )
        return ValidationResult(True)
    if not isinstance(direction, str) or direction not in MOVE_TO_DIRECTIONS:
        return ValidationResult(
            False,
            f'CC-2: move_to.direction "{direction}" ∉ '
            f'{sorted(MOVE_TO_DIRECTIONS)}',
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
    """ADR-0013 Amendment 2026-07-13 — question 단독 계약.

    ``options`` 는 어떤 *의도해석기*·parser 도 채우지 않고(question 전용
    프롬프트·fallback) 어떤 다운스트림도 소비하지 않는다(사용자 응답은
    ``/intent/user_response`` std_msgs/Bool 이진). 부재를 CC-2 reject 사유로
    삼던 종전 검증은 프롬프트/parser 계약과 어긋나는 사문화된 요구 —
    question-only 로 완화. 키가 존재하면 형식은 여전히 검증(list[str]).
    """
    question = theta.get('question')
    if not isinstance(question, str) or not question.strip():
        return ValidationResult(False, 'CC-2: ask_user.question must be non-empty str')
    if 'options' in theta:
        options = theta.get('options')
        if not isinstance(options, list) or any(not isinstance(o, str) for o in options):
            return ValidationResult(False, 'CC-2: ask_user.options must be list[str]')
    return ValidationResult(True)
