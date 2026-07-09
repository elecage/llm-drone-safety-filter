"""ADR-0019 D1 contradicts 술어 7 조건 + D2 5×5 매트릭스 단위 테스트."""

from __future__ import annotations

import pytest

from tier2_gate.contradicts import Activity, contradicts
from tier2_gate.thresholds import DEFAULT


# ---- 픽스처 ----

@pytest.fixture
def both_idle() -> Activity:
    return Activity.IDLE


@pytest.fixture
def inspect_running() -> Activity:
    return Activity.INSPECT


@pytest.fixture
def return_running() -> Activity:
    return Activity.RETURN


# ---- 세션 첫 명령 ----

def test_no_previous_command_never_contradicts(both_idle):
    assert not contradicts(
        None, None, 'move_to', {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- (C1) 위치 변경 모순 ----

def test_c1_micro_adjust_within_D_cancel_not_contradiction(both_idle):
    """ADR-0019 D2 — D_cancel 이하 변위는 의도 수정으로 간주."""
    a = (0.0, 0.0, 1.0)
    b = (0.3, 0.0, 1.0)  # ||A-B|| = 0.3 ≤ 0.5 = D_cancel
    assert not contradicts(
        'move_to', {'position': a, 'max_speed': 0.3},
        'move_to', {'position': b, 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


def test_c1_displacement_above_D_cancel_contradicts(both_idle):
    a = (0.0, 0.0, 1.0)
    b = (1.0, 0.0, 1.0)  # ||A-B|| = 1.0 > 0.5
    assert contradicts(
        'move_to', {'position': a, 'max_speed': 0.3},
        'move_to', {'position': b, 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


def test_c1_boundary_exactly_D_cancel_not_contradiction(both_idle):
    """ADR-0019 D2 — strict > D_cancel, == D_cancel은 모순 아님."""
    a = (0.0, 0.0, 0.0)
    b = (DEFAULT.D_cancel, 0.0, 0.0)
    assert not contradicts(
        'move_to', {'position': a, 'max_speed': 0.3},
        'move_to', {'position': b, 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


def test_c1_3d_diagonal_distance(both_idle):
    """C1의 거리는 3D L2."""
    a = (0.0, 0.0, 0.0)
    b = (0.3, 0.3, 0.3)  # ||A-B|| ≈ 0.52 > 0.5
    assert contradicts(
        'move_to', {'position': a, 'max_speed': 0.3},
        'move_to', {'position': b, 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- (C2) 이동 → 복귀 ----

def test_c2_move_to_then_return_contradicts(both_idle):
    assert contradicts(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3},
        'return_to_dock', {},
        activity=both_idle, thresholds=DEFAULT,
    )


def test_c2_holds_regardless_of_activity(inspect_running):
    """C2는 activity와 무관 (직전 명령 종류로만 결정)."""
    assert contradicts(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3},
        'return_to_dock', {},
        activity=inspect_running, thresholds=DEFAULT,
    )


# ---- (C3) 검사 → 이동, inspect-in-progress ----

def test_c3_inspect_then_move_during_inspect_contradicts(inspect_running):
    assert contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3},
        activity=inspect_running, thresholds=DEFAULT,
    )


def test_c3_inspect_then_move_after_inspect_done_normal(both_idle):
    """inspect 완료 후 (activity=IDLE) 다른 이동은 정상."""
    assert not contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- (C4) 검사 대상 변경 ----

def test_c4_inspect_different_target_during_inspect_contradicts(inspect_running):
    assert contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'inspect', {'target_id': 'mug', 'viewpoint': 'close'},
        activity=inspect_running, thresholds=DEFAULT,
    )


def test_c4_same_target_different_viewpoint_not_contradiction(inspect_running):
    """ADR-0019 D2: X = Y 는 모순 아님 (target 변경만 모순)."""
    assert not contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'inspect', {'target_id': 'sofa', 'viewpoint': 'close'},
        activity=inspect_running, thresholds=DEFAULT,
    )


def test_c4_different_target_after_inspect_done_normal(both_idle):
    assert not contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'inspect', {'target_id': 'mug', 'viewpoint': 'close'},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- (C5) 검사 → 복귀, inspect-in-progress ----

def test_c5_inspect_then_return_during_inspect_contradicts(inspect_running):
    assert contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'return_to_dock', {},
        activity=inspect_running, thresholds=DEFAULT,
    )


def test_c5_inspect_then_return_after_inspect_done_normal(both_idle):
    assert not contradicts(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        'return_to_dock', {},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- (C6) 복귀 → 이동, return-in-progress ----

def test_c6_return_then_move_during_return_contradicts(return_running):
    assert contradicts(
        'return_to_dock', {},
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3},
        activity=return_running, thresholds=DEFAULT,
    )


def test_c6_return_then_move_after_return_done_normal(both_idle):
    assert not contradicts(
        'return_to_dock', {},
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- (C7) 복귀 → 검사, return-in-progress ----

def test_c7_return_then_inspect_during_return_contradicts(return_running):
    assert contradicts(
        'return_to_dock', {},
        'inspect', {'target_id': 'mug', 'viewpoint': 'overview'},
        activity=return_running, thresholds=DEFAULT,
    )


# ---- ADR-0019 D2 매트릭스 — '안전 동작' 칸 (모순 False) ----

@pytest.mark.parametrize('sigma_prev,theta_prev', [
    ('move_to', {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}),
    ('inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}),
    ('return_to_dock', {}),
])
def test_emergency_land_after_any_is_not_contradiction(
    sigma_prev, theta_prev, inspect_running
):
    """ADR-0019 D2 — emergency_land 는 어떤 이전 명령 후에도 모순 아님."""
    assert not contradicts(
        sigma_prev, theta_prev,
        'emergency_land', {},
        activity=inspect_running, thresholds=DEFAULT,
    )


@pytest.mark.parametrize('sigma_prev,theta_prev', [
    ('move_to', {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}),
    ('inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}),
    ('return_to_dock', {}),
])
def test_ask_user_after_any_is_not_contradiction(
    sigma_prev, theta_prev, inspect_running
):
    """ADR-0019 D2 — ask_user 는 모순 처리 대상 아님 (일반 게이트)."""
    assert not contradicts(
        sigma_prev, theta_prev,
        'ask_user', {'question': 'ok?', 'options': []},
        activity=inspect_running, thresholds=DEFAULT,
    )


# ---- ADR-0019 D2 매트릭스 — emergency_land 행 (Φ_2/Φ_3 reject, 모순 아님) ----

@pytest.mark.parametrize('sigma_new,theta_new', [
    ('move_to', {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}),
    ('inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}),
    ('return_to_dock', {}),
])
def test_after_emergency_land_no_contradiction(
    sigma_new, theta_new, inspect_running
):
    """ADR-0019 D2 — emergency_land 후 새 명령은 Φ_2/Φ_3로 reject (contradicts 아님)."""
    assert not contradicts(
        'emergency_land', {},
        sigma_new, theta_new,
        activity=inspect_running, thresholds=DEFAULT,
    )


def test_after_emergency_land_then_emergency_land_normal(both_idle):
    """ADR-0019 D2 — emergency_land 재시도는 정상."""
    assert not contradicts(
        'emergency_land', {},
        'emergency_land', {},
        activity=both_idle, thresholds=DEFAULT,
    )


# ---- ADR-0019 D2 매트릭스 — ask_user 행 (응답 흐름, 모순 아님) ----

@pytest.mark.parametrize('sigma_new,theta_new', [
    ('move_to', {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}),
    ('inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}),
    ('return_to_dock', {}),
    ('emergency_land', {}),
    ('ask_user', {'question': 'q?', 'options': []}),
])
def test_after_ask_user_no_contradiction(
    sigma_new, theta_new, inspect_running
):
    """ADR-0019 D2 — ask_user 후 어떤 새 명령도 응답 흐름 — 모순 아님."""
    assert not contradicts(
        'ask_user', {'question': 'prev?', 'options': []},
        sigma_new, theta_new,
        activity=inspect_running, thresholds=DEFAULT,
    )


# ---- ADR-0019 D2 매트릭스 — 정상 (수정/재시도) 칸 ----

def test_return_then_return_normal_retry(both_idle):
    assert not contradicts(
        'return_to_dock', {},
        'return_to_dock', {},
        activity=both_idle, thresholds=DEFAULT,
    )


def test_move_to_then_inspect_normal(inspect_running):
    """move_to → inspect: ADR-0019 D2 'normal (수정)' 칸."""
    assert not contradicts(
        'move_to', {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3},
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'},
        activity=inspect_running, thresholds=DEFAULT,
    )
