"""ADR-0013 D2 카탈로그 + CC-1/CC-2 단위 테스트."""

from __future__ import annotations

import pytest

from tier2_gate.catalog import (
    CATALOG,
    INSPECT_VIEWPOINTS,
    MOVE_TO_DIRECTIONS,
    SKILL_ACTION_CLASS,
    ActionClass,
    Geofence,
    validate_command,
)


@pytest.fixture
def geofence() -> Geofence:
    """S6 거실 (6×4×2.4) 기준 default geofence."""
    return Geofence(xmin=-3.0, xmax=3.0, ymin=-2.0, ymax=2.0, zmin=0.0, zmax=2.4)


@pytest.fixture
def known() -> frozenset:
    return frozenset({'sofa', 'mug', 'tv'})


# ---- 카탈로그 (D1·D2) ----

def test_catalog_has_exactly_five_skills():
    assert CATALOG == frozenset(
        {'move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'}
    )


def test_action_class_per_skill_matches_adr_0013_d2():
    assert SKILL_ACTION_CLASS['move_to'] == ActionClass.MONITORING
    assert SKILL_ACTION_CLASS['inspect'] == ActionClass.MONITORING
    assert SKILL_ACTION_CLASS['return_to_dock'] == ActionClass.RETURN
    assert SKILL_ACTION_CLASS['emergency_land'] == ActionClass.RETURN
    assert SKILL_ACTION_CLASS['ask_user'] == ActionClass.CONFIRM


def test_inspect_viewpoints_match_adr_0013_d2():
    assert INSPECT_VIEWPOINTS == frozenset({'overview', 'close', 'top'})


def test_move_to_directions_match_sigma_bridge_offsets():
    """ADR-0049 D1 — sigma_bridge `_DIRECTION_OFFSETS`·ESM Table S4와 동기."""
    assert MOVE_TO_DIRECTIONS == frozenset(
        {'forward', 'back', 'left', 'right', 'up', 'down'}
    )


# ---- CC-1 ----

def test_cc1_unknown_skill_rejected(geofence, known):
    result = validate_command('teleport', {}, geofence=geofence, known_objects=known)
    assert not result.valid
    assert 'CC-1' in result.reason


# ---- CC-2: move_to (ADR-0049 D1 — 의미 인자 계약) ----

def test_cc2_move_to_target_id_valid(geofence, known):
    result = validate_command(
        'move_to', {'target_id': 'sofa'},
        geofence=geofence, known_objects=known,
    )
    assert result.valid, result.reason


def test_cc2_move_to_direction_valid(geofence, known):
    result = validate_command(
        'move_to', {'direction': 'forward'},
        geofence=geofence, known_objects=known,
    )
    assert result.valid, result.reason


def test_cc2_move_to_unknown_target_rejected(geofence, known):
    """콘텐츠 검사 — inspect 와 동일 집합. 환각 대체 대상 차단이 move 에도 대칭."""
    result = validate_command(
        'move_to', {'target_id': 'banana'},
        geofence=geofence, known_objects=known,
    )
    assert not result.valid
    assert 'known_objects' in result.reason


def test_cc2_move_to_coordinate_string_target_rejected(geofence, known):
    """감사 실측 — 좌표 문자열 위장 target_id ("(-4.00, 3.00, 2.90)") 는
    장면 미등록이라 콘텐츠 검사가 거부 (지오펜스 위반 좌표 밀반입 차단)."""
    result = validate_command(
        'move_to', {'target_id': '(-4.00, 3.00, 2.90)'},
        geofence=geofence, known_objects=known,
    )
    assert not result.valid


def test_cc2_move_to_invalid_direction_rejected(geofence, known):
    result = validate_command(
        'move_to', {'direction': 'sideways'},
        geofence=geofence, known_objects=known,
    )
    assert not result.valid
    assert 'direction' in result.reason


def test_cc2_move_to_position_categorically_rejected(geofence, known):
    """ADR-0049 D1 — 좌표는 스키마 밖. 지오펜스 안 유효 좌표라도 거부."""
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid
    assert 'position' in result.reason


def test_cc2_move_to_position_alongside_target_rejected(geofence, known):
    """position 이 target_id 와 함께 와도 거부 — 좌표 밀반입 금지."""
    result = validate_command(
        'move_to',
        {'target_id': 'sofa', 'position': (0.0, 0.0, 1.0)},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_move_to_neither_target_nor_direction_rejected(geofence, known):
    result = validate_command(
        'move_to', {}, geofence=geofence, known_objects=known,
    )
    assert not result.valid
    assert 'exactly one' in result.reason


def test_cc2_move_to_both_target_and_direction_rejected(geofence, known):
    result = validate_command(
        'move_to',
        {'target_id': 'sofa', 'direction': 'forward'},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_move_to_target_class_artifact_ignored(geofence, known):
    """감사 실측 210건 형태 — parser 부산물 target_class 는 검증 없이 무시
    (ask_user options 필수화 아티팩트의 재발 방지, ADR-0049 D1)."""
    result = validate_command(
        'move_to',
        {'target_class': 'cup', 'target_id': 'mug'},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid, result.reason


# ---- CC-2: inspect ----

def test_cc2_inspect_valid(geofence, known):
    result = validate_command(
        'inspect',
        {'target_id': 'sofa', 'viewpoint': 'overview'},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid


def test_cc2_inspect_unknown_target(geofence, known):
    result = validate_command(
        'inspect',
        {'target_id': 'banana', 'viewpoint': 'overview'},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid
    assert 'known_objects' in result.reason


def test_cc2_inspect_invalid_viewpoint(geofence, known):
    result = validate_command(
        'inspect',
        {'target_id': 'sofa', 'viewpoint': 'side'},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid
    assert 'viewpoint' in result.reason


# ---- CC-2: return_to_dock / emergency_land ----

@pytest.mark.parametrize('sigma', ['return_to_dock', 'emergency_land'])
def test_cc2_empty_theta_valid(sigma, geofence, known):
    result = validate_command(sigma, {}, geofence=geofence, known_objects=known)
    assert result.valid


@pytest.mark.parametrize('sigma', ['return_to_dock', 'emergency_land'])
def test_cc2_nonempty_theta_rejected(sigma, geofence, known):
    result = validate_command(
        sigma, {'extra': 1}, geofence=geofence, known_objects=known
    )
    assert not result.valid


# ---- CC-2: ask_user ----

def test_cc2_ask_user_valid(geofence, known):
    result = validate_command(
        'ask_user',
        {'question': 'Did you mean A or B?', 'options': ['A', 'B']},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid


def test_cc2_ask_user_empty_options_ok(geofence, known):
    """1차 시안: options 빈 리스트는 허용 (paper-1 free-response 가능)."""
    result = validate_command(
        'ask_user',
        {'question': 'Confirm?', 'options': []},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid


def test_cc2_ask_user_no_options_key_ok(geofence, known):
    """ADR-0013 Amendment 2026-07-13 — question-only 계약. 실 프롬프트/parser 는
    options 를 아예 채우지 않으므로(question 전용) 키 부재를 accept."""
    result = validate_command(
        'ask_user',
        {'question': 'Confirm?'},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid, result.reason


def test_cc2_ask_user_empty_question(geofence, known):
    result = validate_command(
        'ask_user',
        {'question': '   ', 'options': []},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_ask_user_options_not_list(geofence, known):
    result = validate_command(
        'ask_user',
        {'question': 'q?', 'options': 'A or B'},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_ask_user_options_mixed_types(geofence, known):
    result = validate_command(
        'ask_user',
        {'question': 'q?', 'options': ['A', 1]},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


# ---- extra keys forward compat ----

def test_cc2_move_to_extra_keys_ignored(geofence, known):
    """move_to 의 인자 검증은 target_id/direction/position 만 — 미래 키 (예: yaw,
    hint)는 무시. A4-2 단계에서 추가 인자 도입 시 catalog 변경 없이 통과해야 함.
    """
    result = validate_command(
        'move_to',
        {'target_id': 'sofa', 'yaw': 1.57, 'hint': 'fast'},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid, result.reason


def test_cc2_inspect_extra_keys_ignored(geofence, known):
    result = validate_command(
        'inspect',
        {'target_id': 'sofa', 'viewpoint': 'overview', 'priority': 'high'},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid


def test_cc2_ask_user_extra_keys_ignored(geofence, known):
    result = validate_command(
        'ask_user',
        {'question': 'q?', 'options': ['A', 'B'], 'timeout_hint': 10.0},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid


# ---- Geofence ----

def test_geofence_contains_corner(geofence):
    """경계 inclusive — corner 8개 모두 contains."""
    assert geofence.contains((3.0, 2.0, 2.4))
    assert geofence.contains((-3.0, -2.0, 0.0))
    assert not geofence.contains((3.01, 0.0, 1.0))
    assert not geofence.contains((0.0, 0.0, -0.01))


def test_geofence_inclusive_at_each_face(geofence):
    """면 위의 점도 inclusive — 각 면의 정중앙 1점씩."""
    assert geofence.contains((geofence.xmax, 0.0, 1.2))  # +x face
    assert geofence.contains((geofence.xmin, 0.0, 1.2))  # -x face
    assert geofence.contains((0.0, geofence.ymax, 1.2))  # +y face
    assert geofence.contains((0.0, geofence.ymin, 1.2))  # -y face
    assert geofence.contains((0.0, 0.0, geofence.zmax))  # +z face
    assert geofence.contains((0.0, 0.0, geofence.zmin))  # -z face


@pytest.mark.parametrize('bad', [
    {'xmin': 1.0, 'xmax': 0.0, 'ymin': -1.0, 'ymax': 1.0, 'zmin': 0.0, 'zmax': 1.0},
    {'xmin': 0.0, 'xmax': 1.0, 'ymin': 1.0, 'ymax': -1.0, 'zmin': 0.0, 'zmax': 1.0},
    {'xmin': 0.0, 'xmax': 1.0, 'ymin': -1.0, 'ymax': 1.0, 'zmin': 1.0, 'zmax': 0.0},
])
def test_geofence_inverted_bounds_rejected(bad):
    """xmin > xmax (또는 y/z) 인 invalid geofence 는 생성 시 AssertionError."""
    with pytest.raises(AssertionError):
        Geofence(**bad)


def test_geofence_degenerate_zero_volume_allowed():
    """xmin == xmax (zero-thickness slab) 는 허용 — 강제 동등한 점 1개로 수렴."""
    gf = Geofence(xmin=1.0, xmax=1.0, ymin=-1.0, ymax=1.0, zmin=0.0, zmax=1.0)
    assert gf.contains((1.0, 0.0, 0.5))
    assert not gf.contains((1.01, 0.0, 0.5))


# ---- ADR-0049 계약 호환 — 게이트 수용 형태 ⊆ sigma_bridge 처리 형태 ----

def test_move_to_directions_sync_with_sigma_bridge_offsets():
    """게이트가 수용하는 direction 토큰은 sigma_bridge 가 전부 해소 가능해야
    함 (ADR-0049 D1/D2 — 게이트 스키마 ⊆ sigma_bridge 수용 범위)."""
    helpers = pytest.importorskip('intent_sigma_bridge.sigma_bridge_helpers')
    assert MOVE_TO_DIRECTIONS == frozenset(helpers._DIRECTION_OFFSETS)
