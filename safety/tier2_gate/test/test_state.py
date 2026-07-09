"""state.py GateSession transition 단위 테스트 (M1·M2·M3 contract)."""

from __future__ import annotations

import pytest

from tier2_gate.contradicts import Activity
from tier2_gate.state import GateSession
from tier2_gate.thresholds import DEFAULT


# ---- 초기 상태 ----

def test_initial_state_idle_no_history():
    s = GateSession()
    assert s.activity == Activity.IDLE
    assert s.sigma_prev is None
    assert s.theta_prev is None
    assert s.n_sc == 0
    assert s.confirm_fired_at is None
    assert s.user_confirmed is False


def test_to_gate_state_no_pending_confirm_elapsed_none():
    s = GateSession()
    gs = s.to_gate_state(now=100.0)
    assert gs.confirm_pending_elapsed_s is None


def test_to_gate_state_elapsed_computed_from_confirm_fired_at():
    s = GateSession(confirm_fired_at=100.0)
    gs = s.to_gate_state(now=125.0)
    assert gs.confirm_pending_elapsed_s == 25.0


def test_to_gate_state_propagates_sensor_inputs():
    s = GateSession(
        battery_pct=42.0,
        link_lost=True,
        tier1_active=False,
        user_confirmed=True,
        n_sc=2,
    )
    gs = s.to_gate_state(now=0.0)
    assert gs.battery_pct == 42.0
    assert gs.link_lost is True
    assert gs.tier1_active is False
    assert gs.user_confirmed is True
    assert gs.n_sc == 2


# ---- on_accept — M3 reset + activity 전이 ----

def test_on_accept_move_to_keeps_activity_idle_resets_n_sc():
    s = GateSession(activity=Activity.IDLE, n_sc=2, user_confirmed=True)
    s.on_accept('move_to', {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3})
    assert s.sigma_prev == 'move_to'
    assert s.theta_prev == {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}
    assert s.n_sc == 0  # M3 reset
    assert s.user_confirmed is False  # 단발성 reset
    assert s.activity == Activity.IDLE


def test_on_accept_inspect_transitions_to_inspect():
    s = GateSession()
    s.on_accept('inspect', {'target_id': 'sofa', 'viewpoint': 'overview'})
    assert s.activity == Activity.INSPECT
    assert s.settle_started_at is None  # 새 inspect 시작 — 안정 타이머 reset


def test_on_accept_return_to_dock_transitions_to_return():
    s = GateSession()
    s.on_accept('return_to_dock', {})
    assert s.activity == Activity.RETURN


def test_on_accept_emergency_land_goes_idle():
    """비상 동작은 in-progress 추적 대상 아님."""
    s = GateSession(activity=Activity.INSPECT, settle_started_at=10.0)
    s.on_accept('emergency_land', {})
    assert s.activity == Activity.IDLE
    assert s.settle_started_at is None


def test_on_accept_clears_confirm_pending():
    s = GateSession(confirm_fired_at=100.0)
    s.on_accept('move_to', {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3})
    assert s.confirm_fired_at is None


def test_on_accept_copies_theta_defensively():
    """theta 가 caller 가 mutate 해도 session 의 theta_prev 는 안 변해야."""
    theta = {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}
    s = GateSession()
    s.on_accept('move_to', theta)
    theta['position'] = (9.9, 9.9, 9.9)
    assert s.theta_prev == {'position': (1.0, 0.0, 1.0), 'max_speed': 0.3}


# ---- on_confirm / on_user_response — M2 ----

def test_on_confirm_sets_timestamp():
    s = GateSession()
    s.on_confirm(now=42.0)
    assert s.confirm_fired_at == 42.0


def test_on_user_response_clears_timer_and_sets_flag():
    s = GateSession(confirm_fired_at=50.0)
    s.on_user_response(True)
    assert s.confirm_fired_at is None
    assert s.user_confirmed is True


def test_on_user_response_decline_clears_timer():
    s = GateSession(confirm_fired_at=50.0, user_confirmed=True)
    s.on_user_response(False)
    assert s.confirm_fired_at is None
    assert s.user_confirmed is False


# ---- on_self_correction — Φ_8 입력 ----

def test_on_self_correction_increments_n_sc():
    s = GateSession()
    s.on_self_correction()
    s.on_self_correction()
    assert s.n_sc == 2


def test_n_sc_resets_at_next_accept():
    """ACCEPT 직후 self-correction 누적은 reset (M3 boundary)."""
    s = GateSession()
    s.on_self_correction()
    s.on_self_correction()
    assert s.n_sc == 2
    s.on_accept('move_to', {'position': (0.0, 0.0, 1.0), 'max_speed': 0.3})
    assert s.n_sc == 0


# ---- update_activity_progress — ADR-0019 D3 ----

def test_update_no_drone_pos_is_noop():
    s = GateSession(activity=Activity.RETURN)
    s.update_activity_progress(thresholds=DEFAULT, now=0.0)
    assert s.activity == Activity.RETURN  # 변화 없음


def test_update_idle_is_noop():
    s = GateSession(
        activity=Activity.IDLE,
        drone_pos_enu=(0.0, 0.0, 0.0),
    )
    s.update_activity_progress(thresholds=DEFAULT, now=0.0)
    assert s.activity == Activity.IDLE


def test_update_return_inside_eps_dock_completes():
    s = GateSession(
        activity=Activity.RETURN,
        drone_pos_enu=(0.05, 0.0, 0.05),
        dock_pos_enu=(0.0, 0.0, 0.0),
    )
    # ||drone - dock|| ≈ 0.0707 < eps_dock=0.2 → IDLE
    s.update_activity_progress(thresholds=DEFAULT, now=0.0)
    assert s.activity == Activity.IDLE


def test_update_return_outside_eps_dock_stays():
    s = GateSession(
        activity=Activity.RETURN,
        drone_pos_enu=(1.0, 0.0, 0.0),
        dock_pos_enu=(0.0, 0.0, 0.0),
    )
    s.update_activity_progress(thresholds=DEFAULT, now=0.0)
    assert s.activity == Activity.RETURN


def test_update_inspect_settle_pending_then_complete():
    """eps_vp 안에 들어가면 settle_started_at set, tau_settle 경과 후 IDLE."""
    s = GateSession(
        activity=Activity.INSPECT,
        drone_pos_enu=(1.0, 0.0, 1.0),
        target_poses={'sofa': (1.05, 0.0, 1.0)},
        theta_prev={'target_id': 'sofa', 'viewpoint': 'overview'},
        sigma_prev='inspect',
    )
    # ||drone-target||=0.05 < eps_vp=0.1 → settle 시작 (now=100)
    s.update_activity_progress(thresholds=DEFAULT, now=100.0)
    assert s.activity == Activity.INSPECT
    assert s.settle_started_at == 100.0

    # tau_settle=1.0 경과 전 — INSPECT 유지
    s.update_activity_progress(thresholds=DEFAULT, now=100.5)
    assert s.activity == Activity.INSPECT

    # tau_settle 경과 — IDLE 전이
    s.update_activity_progress(thresholds=DEFAULT, now=101.5)
    assert s.activity == Activity.IDLE
    assert s.settle_started_at is None


def test_update_inspect_leaves_viewpoint_resets_settle():
    """viewpoint 벗어나면 settle 타이머 reset."""
    s = GateSession(
        activity=Activity.INSPECT,
        drone_pos_enu=(1.0, 0.0, 1.0),
        target_poses={'sofa': (1.05, 0.0, 1.0)},
        theta_prev={'target_id': 'sofa', 'viewpoint': 'overview'},
        sigma_prev='inspect',
        settle_started_at=99.5,  # 이미 진행 중이었음
    )
    # 갑자기 멀어짐
    s.drone_pos_enu = (2.0, 0.0, 1.0)
    s.update_activity_progress(thresholds=DEFAULT, now=100.0)
    assert s.activity == Activity.INSPECT  # 유지
    assert s.settle_started_at is None  # reset


def test_update_inspect_unknown_target_is_noop():
    """target_poses 에 없는 target_id 면 진행 종료 못함 (외부 추적 누락)."""
    s = GateSession(
        activity=Activity.INSPECT,
        drone_pos_enu=(0.0, 0.0, 0.0),
        target_poses={},  # 비어 있음
        theta_prev={'target_id': 'sofa', 'viewpoint': 'overview'},
        sigma_prev='inspect',
    )
    s.update_activity_progress(thresholds=DEFAULT, now=0.0)
    assert s.activity == Activity.INSPECT  # 변화 없음


# ---- 통합 — gate cycle 시뮬레이션 ----

def test_full_accept_cycle_through_inspect_completes_to_idle():
    """가상 시나리오: ACCEPT inspect → drone 도달 → settle 경과 → IDLE."""
    s = GateSession(target_poses={'mug': (0.5, 0.5, 0.8)})
    s.on_accept('inspect', {'target_id': 'mug', 'viewpoint': 'close'})
    assert s.activity == Activity.INSPECT

    # 드론이 viewpoint 도달
    s.drone_pos_enu = (0.5, 0.5, 0.8)
    s.update_activity_progress(thresholds=DEFAULT, now=10.0)
    assert s.activity == Activity.INSPECT
    assert s.settle_started_at == 10.0

    # tau_settle 경과
    s.update_activity_progress(thresholds=DEFAULT, now=11.5)
    assert s.activity == Activity.IDLE
