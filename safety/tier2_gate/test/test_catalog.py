"""ADR-0013 D2 카탈로그 + CC-1/CC-2 단위 테스트."""

from __future__ import annotations

import pytest

from tier2_gate.catalog import (
    CATALOG,
    INSPECT_VIEWPOINTS,
    MOVE_TO_MAX_SPEED_HI,
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


def test_max_speed_hi_matches_adr_0013_d2():
    assert MOVE_TO_MAX_SPEED_HI == 0.5


# ---- CC-1 ----

def test_cc1_unknown_skill_rejected(geofence, known):
    result = validate_command('teleport', {}, geofence=geofence, known_objects=known)
    assert not result.valid
    assert 'CC-1' in result.reason


# ---- CC-2: move_to ----

def test_cc2_move_to_valid(geofence, known):
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence,
        known_objects=known,
    )
    assert result.valid, result.reason


def test_cc2_move_to_outside_geofence(geofence, known):
    result = validate_command(
        'move_to',
        {'position': (5.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid
    assert 'geofence' in result.reason


def test_cc2_move_to_speed_above_max(geofence, known):
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': 0.6},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid
    assert 'max_speed' in result.reason


def test_cc2_move_to_negative_speed(geofence, known):
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': -0.1},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_move_to_missing_position(geofence, known):
    result = validate_command(
        'move_to', {'max_speed': 0.3},
        geofence=geofence, known_objects=known,
    )
    assert not result.valid
    assert 'position' in result.reason


def test_cc2_move_to_position_not_3tuple(geofence, known):
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0), 'max_speed': 0.3},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_move_to_speed_must_be_numeric(geofence, known):
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': '0.3'},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


def test_cc2_move_to_speed_bool_rejected(geofence, known):
    """isinstance(True, int) 함정 — bool은 명시적으로 거부."""
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': True},
        geofence=geofence,
        known_objects=known,
    )
    assert not result.valid


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
    """move_to 의 인자 검증은 position/max_speed 만 — 미래 키 (예: yaw, hint)는 무시.

    A4-2 단계에서 추가 인자 도입 시 catalog 변경 없이 통과해야 함.
    """
    result = validate_command(
        'move_to',
        {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3, 'yaw': 1.57, 'hint': 'fast'},
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
