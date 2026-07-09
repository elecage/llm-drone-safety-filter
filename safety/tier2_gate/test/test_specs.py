"""cmsm-proof §9.3 Φ_1 … Φ_9 단위 테스트 (Φ_4·Φ_10 은 gate 측에서 처분)."""

from __future__ import annotations

import pytest

from tier2_gate.catalog import Geofence
from tier2_gate.specs import (
    GateState,
    check_phi_1,
    check_phi_2,
    check_phi_3,
    check_phi_5,
    check_phi_6,
    check_phi_7,
    check_phi_8,
    check_phi_9,
    violations,
)
from tier2_gate.thresholds import DEFAULT


@pytest.fixture
def geofence() -> Geofence:
    return Geofence(xmin=-3.0, xmax=3.0, ymin=-2.0, ymax=2.0, zmin=0.0, zmax=2.4)


@pytest.fixture
def state() -> GateState:
    """정상 상태 — 모든 사양 통과."""
    return GateState(
        battery_pct=80.0,
        link_lost=False,
        tier1_active=True,
        user_confirmed=False,
        n_sc=0,
        confirm_pending_elapsed_s=None,
    )


# ---- Φ_1 geofence ----

def test_phi_1_move_to_inside_geofence_ok(geofence):
    assert not check_phi_1(
        'move_to', {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3}, geofence=geofence
    )


def test_phi_1_move_to_outside_geofence_violated(geofence):
    assert check_phi_1(
        'move_to', {'position': (5.0, 0.0, 1.0), 'max_speed': 0.3}, geofence=geofence
    )


@pytest.mark.parametrize('sigma', ['inspect', 'return_to_dock', 'emergency_land', 'ask_user'])
def test_phi_1_non_move_to_skills_pass(sigma, geofence):
    """Φ_1 은 move_to 만 게이트 측에서 evaluate — 그 외는 Tier 1 위임."""
    assert not check_phi_1(sigma, {}, geofence=geofence)


# ---- Φ_2 action-class ----

def test_phi_2_known_skills_pass():
    for sigma in ('move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'):
        assert not check_phi_2(sigma)


def test_phi_2_unknown_skill_violated():
    assert check_phi_2('teleport')


# ---- Φ_3 user-confirmed ----

def test_phi_3_monitoring_always_ok(state):
    """move_to, inspect 는 user_confirmed 무관 — auto OK."""
    assert not check_phi_3('move_to', state)
    assert not check_phi_3('inspect', state)


def test_phi_3_return_without_confirm_violated(state):
    """return_to_dock, emergency_land — user_confirmed 강제."""
    assert check_phi_3('return_to_dock', state)
    assert check_phi_3('emergency_land', state)


def test_phi_3_return_with_confirm_ok():
    confirmed = GateState(user_confirmed=True)
    assert not check_phi_3('return_to_dock', confirmed)
    assert not check_phi_3('emergency_land', confirmed)


def test_phi_3_ask_user_exempt(state):
    """ask_user 는 게이트 내부 응답 — Φ_3 면제 (paper-1 narrow 해석)."""
    assert not check_phi_3('ask_user', state)


# ---- Φ_5 battery RTL ----

def test_phi_5_normal_battery_ok(state):
    for sigma in ('move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'):
        assert not check_phi_5(sigma, state=state, thresholds=DEFAULT)


def test_phi_5_low_battery_blocks_non_return():
    low = GateState(battery_pct=20.0)
    assert check_phi_5('move_to', state=low, thresholds=DEFAULT)
    assert check_phi_5('inspect', state=low, thresholds=DEFAULT)
    assert check_phi_5('ask_user', state=low, thresholds=DEFAULT)


def test_phi_5_low_battery_allows_return_emergency():
    low = GateState(battery_pct=20.0)
    assert not check_phi_5('return_to_dock', state=low, thresholds=DEFAULT)
    assert not check_phi_5('emergency_land', state=low, thresholds=DEFAULT)


def test_phi_5_boundary_at_B_rtl_ok():
    """ADR-0013 D5 B_rtl=30 — battery == B_rtl 은 아직 위반 아님 (strict <)."""
    at = GateState(battery_pct=DEFAULT.B_rtl)
    assert not check_phi_5('move_to', state=at, thresholds=DEFAULT)


# ---- Φ_6 link-loss ----

def test_phi_6_link_intact_ok(state):
    for sigma in ('move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'):
        assert not check_phi_6(sigma, state=state)


def test_phi_6_link_lost_blocks_non_emergency():
    lost = GateState(link_lost=True)
    assert check_phi_6('move_to', state=lost)
    assert check_phi_6('inspect', state=lost)
    assert check_phi_6('return_to_dock', state=lost)
    assert check_phi_6('ask_user', state=lost)


def test_phi_6_link_lost_allows_emergency_land():
    lost = GateState(link_lost=True)
    assert not check_phi_6('emergency_land', state=lost)


# ---- Φ_7 tier1-active ----

def test_phi_7_tier1_active_ok(state):
    assert not check_phi_7(state)


def test_phi_7_tier1_inactive_violated():
    dead = GateState(tier1_active=False)
    assert check_phi_7(dead)


# ---- Φ_8 n_sc ----

def test_phi_8_below_N_sc_ok(state):
    """default n_sc=0 < N_sc=3."""
    assert not check_phi_8(state=state, thresholds=DEFAULT)


def test_phi_8_at_N_sc_violated():
    """ADR-0013 D5 N_sc=3 — n_sc=3 은 위반 (strict <)."""
    at = GateState(n_sc=DEFAULT.N_sc)
    assert check_phi_8(state=at, thresholds=DEFAULT)


def test_phi_8_above_N_sc_violated():
    above = GateState(n_sc=DEFAULT.N_sc + 1)
    assert check_phi_8(state=above, thresholds=DEFAULT)


# ---- Φ_9 confirm timeout ----

def test_phi_9_no_pending_confirm_ok(state):
    assert not check_phi_9(state=state, thresholds=DEFAULT)


def test_phi_9_pending_within_T_resp_ok():
    within = GateState(confirm_pending_elapsed_s=DEFAULT.T_resp / 2)
    assert not check_phi_9(state=within, thresholds=DEFAULT)


def test_phi_9_pending_at_T_resp_ok():
    """ADR-0013 D5 T_resp=30 — elapsed == T_resp 은 아직 위반 아님 (strict >)."""
    at = GateState(confirm_pending_elapsed_s=DEFAULT.T_resp)
    assert not check_phi_9(state=at, thresholds=DEFAULT)


def test_phi_9_pending_above_T_resp_violated():
    above = GateState(confirm_pending_elapsed_s=DEFAULT.T_resp + 0.1)
    assert check_phi_9(state=above, thresholds=DEFAULT)


# ---- violations() 통합 ----

def test_violations_empty_on_clean_dispatch(geofence, state):
    """정상 상태에서 monitoring 명령은 어떤 Φ도 위반 안 함."""
    assert violations(
        'move_to', {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence, state=state, thresholds=DEFAULT,
    ) == []


def test_violations_excludes_phi_4_and_phi_10(geofence, state):
    """cmsm-proof §9.4 — Φ_4·Φ_10 은 본 함수의 결과에 절대 포함 안 됨."""
    # 어떤 입력을 줘도 'Φ_4'·'Φ_10' 문자열은 출력에 없음.
    result = violations(
        'move_to', {'position': (5.0, 0.0, 1.0), 'max_speed': 0.3},  # geofence 위반
        geofence=geofence, state=state, thresholds=DEFAULT,
    )
    assert 'Φ_4' not in result
    assert 'Φ_10' not in result


def test_violations_geofence_returns_phi_1(geofence, state):
    result = violations(
        'move_to', {'position': (5.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence, state=state, thresholds=DEFAULT,
    )
    assert result == ['Φ_1']


def test_violations_unknown_skill_returns_phi_2(geofence, state):
    result = violations(
        'teleport', {}, geofence=geofence, state=state, thresholds=DEFAULT,
    )
    assert 'Φ_2' in result


def test_violations_multiple_concurrent(geofence):
    """배터리 낮음 + Tier 1 dead + confirm timeout 동시 — 3건 모두 보고."""
    bad = GateState(
        battery_pct=20.0,
        tier1_active=False,
        confirm_pending_elapsed_s=DEFAULT.T_resp + 1.0,
    )
    result = violations(
        'move_to', {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence, state=bad, thresholds=DEFAULT,
    )
    assert set(result) >= {'Φ_5', 'Φ_7', 'Φ_9'}


def test_violations_link_lost_with_emergency_land_ok(geofence):
    """link-loss 상태에서 emergency_land 는 Φ_6 비위반."""
    lost = GateState(link_lost=True)
    result = violations(
        'emergency_land', {}, geofence=geofence, state=lost, thresholds=DEFAULT,
    )
    assert 'Φ_6' not in result
