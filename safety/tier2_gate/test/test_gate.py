"""cmsm-proof §9.4 게이트 결정 함수 G 단위 테스트.

5 cases × 입력 조합으로 accept/confirm/reject 분기 모두 cover.
"""

from __future__ import annotations

import pytest

from tier2_gate.catalog import Geofence
from tier2_gate.contradicts import Activity
from tier2_gate.gate import Decision, gate
from tier2_gate.specs import GateState
from tier2_gate.thresholds import DEFAULT


@pytest.fixture
def geofence() -> Geofence:
    return Geofence(xmin=-3.0, xmax=3.0, ymin=-2.0, ymax=2.0, zmin=0.0, zmax=2.4)


@pytest.fixture
def known() -> frozenset:
    return frozenset({'sofa', 'mug', 'tv'})


@pytest.fixture
def idle() -> Activity:
    return Activity.IDLE


@pytest.fixture
def healthy_state() -> GateState:
    """모든 사양 통과 + ask_user 응답 = confirmed."""
    return GateState(
        battery_pct=80.0,
        link_lost=False,
        tier1_active=True,
        user_confirmed=True,
        n_sc=0,
        confirm_pending_elapsed_s=None,
    )


def _call(sigma, theta, c, *, geofence, known, idle, state,
          sigma_prev=None, theta_prev=None, thresholds=DEFAULT):
    return gate(
        sigma, theta, c,
        sigma_prev=sigma_prev, theta_prev=theta_prev,
        activity=idle,
        geofence=geofence, known_objects=known,
        state=state, thresholds=thresholds,
    )


# ---- Case 6 ACCEPT ----

def test_case6_monitoring_high_confidence_accept(geofence, known, idle, healthy_state):
    """move_to (monitoring), c >= c_hi, valid CC, no spec violation → accept."""
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.9,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


def test_case6_monitoring_in_confirm_band_still_accept(geofence, known, idle, healthy_state):
    """monitoring 클래스는 c ∈ [c_lo, c_hi) 여도 accept (case 5 조건 미충족)."""
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.5,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


def test_case6_return_with_high_confidence_accept(geofence, known, idle, healthy_state):
    """return_to_dock + user_confirmed + c >= c_hi → accept."""
    r = _call(
        'return_to_dock', {}, 0.9,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


# ---- Case 5 CONFIRM (c in [c_lo, c_hi) ∧ non-monitoring) ----

def test_case5_return_in_confirm_band_triggers_confirm(geofence, known, idle, healthy_state):
    r = _call(
        'return_to_dock', {}, 0.5,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.CONFIRM
    assert 'monitoring' in r.reason


def test_case5_emergency_land_in_confirm_band_triggers_confirm(geofence, known, idle, healthy_state):
    r = _call(
        'emergency_land', {}, 0.5,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.CONFIRM


def test_case5_boundary_at_c_hi_accepts_non_monitoring(geofence, known, idle, healthy_state):
    """c == c_hi 는 strict < c_hi 아님 → case 5 미발동 → accept."""
    r = _call(
        'return_to_dock', {}, DEFAULT.c_hi,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


# ---- Case 4 CONFIRM (Φ_10 contradicts) ----

def test_case4_contradicts_overrides_high_confidence_to_confirm(
    geofence, known, idle, healthy_state
):
    """High c + valid command but contradicts σ_prev → confirm (not accept)."""
    r = _call(
        'return_to_dock', {}, 0.95,
        sigma_prev='move_to', theta_prev={'position': (1.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.CONFIRM
    assert 'Φ_10' in r.violations


def test_case4_no_contradicts_passes_through(geofence, known, idle, healthy_state):
    """compatible sequence (move_to → inspect, activity IDLE) → accept."""
    r = _call(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}, 0.9,
        sigma_prev='move_to', theta_prev={'position': (1.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


# ---- Case 3 REJECT (spec violation) ----

def test_case3_geofence_violation_rejects(geofence, known, idle, healthy_state):
    r = _call(
        'move_to', {'position': (5.0, 0.0, 1.0), 'max_speed': 0.3}, 0.9,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    # 이 입력은 CC-2 (geofence)가 먼저 잡음 → case 1 reject.
    # Φ_1 검증은 catalog 동치 — 어느 case에서 잡혀도 reject 결과 동일.
    assert r.decision == Decision.REJECT


def test_case3_return_without_confirmation_rejects(geofence, known, idle):
    """user_confirmed=False 인 상태에서 return_to_dock → Φ_3 reject."""
    unconfirmed = GateState(
        battery_pct=80.0, link_lost=False, tier1_active=True, user_confirmed=False,
    )
    r = _call(
        'return_to_dock', {}, 0.9,
        geofence=geofence, known=known, idle=idle, state=unconfirmed,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_3' in r.violations


def test_case3_low_battery_blocks_monitoring(geofence, known, idle):
    low_battery = GateState(
        battery_pct=20.0, tier1_active=True, user_confirmed=True,
    )
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.9,
        geofence=geofence, known=known, idle=idle, state=low_battery,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_5' in r.violations


def test_case3_low_battery_allows_return(geofence, known, idle):
    low_battery = GateState(
        battery_pct=20.0, tier1_active=True, user_confirmed=True,
    )
    r = _call(
        'return_to_dock', {}, 0.9,
        geofence=geofence, known=known, idle=idle, state=low_battery,
    )
    assert r.decision == Decision.ACCEPT


def test_case3_link_lost_only_allows_emergency_land(geofence, known, idle):
    lost = GateState(
        link_lost=True, tier1_active=True, user_confirmed=True, battery_pct=80.0,
    )
    r_em = _call(
        'emergency_land', {}, 0.9,
        geofence=geofence, known=known, idle=idle, state=lost,
    )
    r_ret = _call(
        'return_to_dock', {}, 0.9,
        geofence=geofence, known=known, idle=idle, state=lost,
    )
    assert r_em.decision == Decision.ACCEPT
    assert r_ret.decision == Decision.REJECT
    assert 'Φ_6' in r_ret.violations


def test_case3_tier1_dead_blocks_everything(geofence, known, idle):
    dead = GateState(
        tier1_active=False, user_confirmed=True, battery_pct=80.0,
    )
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.9,
        geofence=geofence, known=known, idle=idle, state=dead,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_7' in r.violations


def test_case3_n_sc_threshold_blocks(geofence, known, idle):
    saturated = GateState(
        n_sc=DEFAULT.N_sc, user_confirmed=True, battery_pct=80.0,
    )
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.9,
        geofence=geofence, known=known, idle=idle, state=saturated,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_8' in r.violations


def test_case3_confirm_timeout_blocks(geofence, known, idle):
    timed_out = GateState(
        confirm_pending_elapsed_s=DEFAULT.T_resp + 1.0,
        user_confirmed=True, battery_pct=80.0,
    )
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.9,
        geofence=geofence, known=known, idle=idle, state=timed_out,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_9' in r.violations


# ---- Case 2 REJECT (c < c_lo, Φ_4) ----

def test_case2_low_confidence_rejects(geofence, known, idle, healthy_state):
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.3,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_4' in r.violations


def test_case2_boundary_at_c_lo_passes_to_next_case(geofence, known, idle, healthy_state):
    """c == c_lo 는 strict < c_lo 아님 → case 2 미발동, 다음 case 진입."""
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, DEFAULT.c_lo,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    # monitoring 이므로 case 5 미발동 → accept.
    assert r.decision == Decision.ACCEPT


# ---- Case 2 inspect 면제 (ADR-0034 — gate-before-vantage 교착) ----

def test_case2_inspect_low_confidence_accepts(geofence, known, idle, healthy_state):
    """inspect 는 Φ_4 면제 → c < c_lo 여도 reject 안 함 (관측-grounding 순환 차단)."""
    r = _call(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}, 0.3,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


def test_case2_inspect_zero_confidence_accepts(geofence, known, idle, healthy_state):
    """cold-start: 관측 전 s1=0 → c=0 인 inspect 도 accept → vantage 비행 가능.

    A2 핵심 — 종전엔 c=0 → Φ_4 reject → σ 미전달 → 영영 grounding 불가(교착)."""
    r = _call(
        'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}, 0.0,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.ACCEPT


def test_case2_move_to_low_confidence_still_rejects(geofence, known, idle, healthy_state):
    """A2 는 inspect 만 면제 — move_to(명시 위치)는 저신뢰도 reject 유지
    (적대적 move_to 의 게이트 방어 보존)."""
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.3,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_4' in r.violations


def test_case2_inspect_invalid_target_still_rejects(geofence, known, idle, healthy_state):
    """Φ_4 면제는 *신뢰도* 만 — inspect 라도 CC-2(미지 대상) 등 하드 검사는 그대로
    (Case 1 이 Case 2 보다 먼저). 면제가 안전 검사를 무력화하지 않음."""
    r = _call(
        'inspect', {'target_id': 'banana', 'viewpoint': 'overview'}, 0.0,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.REJECT
    assert 'CC-2' in r.reason


# ---- Case 1 REJECT (CC-1/CC-2) ----

def test_case1_unknown_skill_rejects(geofence, known, idle, healthy_state):
    r = _call(
        'teleport', {}, 0.9,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.REJECT
    assert 'CC-1' in r.reason


def test_case1_bad_arg_rejects(geofence, known, idle, healthy_state):
    r = _call(
        'inspect', {'target_id': 'banana', 'viewpoint': 'overview'}, 0.9,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.REJECT
    assert 'CC-2' in r.reason


# ---- Case 순서 (case 1 우선) ----

def test_case1_precedes_case2(geofence, known, idle, healthy_state):
    """CC 위반 + 저신뢰도 동시 → case 1 (CC) 가 먼저 발동."""
    r = _call(
        'teleport', {}, 0.1,
        geofence=geofence, known=known, idle=idle, state=healthy_state,
    )
    assert r.decision == Decision.REJECT
    assert 'CC-1' in r.reason  # Φ_4 아님


def test_case2_precedes_case3(geofence, known, idle):
    """저신뢰도 + 사양 위반 동시 → case 2 (Φ_4) 가 먼저 발동."""
    dead = GateState(tier1_active=False, battery_pct=80.0, user_confirmed=True)
    r = _call(
        'move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.1,
        geofence=geofence, known=known, idle=idle, state=dead,
    )
    assert r.decision == Decision.REJECT
    assert 'Φ_4' in r.violations
    assert 'Φ_7' not in r.violations  # case 3 미실행


def test_case3_precedes_case4(geofence, known, idle):
    """사양 위반 + contradicts 동시 → case 3 (reject) 가 먼저 발동."""
    dead = GateState(tier1_active=False, battery_pct=80.0, user_confirmed=True)
    r = _call(
        'return_to_dock', {}, 0.9,
        sigma_prev='move_to', theta_prev={'position': (1.0, 0.0, 1.0), 'max_speed': 0.3},
        geofence=geofence, known=known, idle=idle, state=dead,
    )
    assert r.decision == Decision.REJECT  # contradicts confirm 아님
    assert 'Φ_7' in r.violations


# ---- pure function 보증 ----

def test_gate_is_pure(geofence, known, idle, healthy_state):
    """같은 입력 N회 호출 시 같은 출력."""
    args = ('move_to', {'position': (1.0, 1.0, 1.0), 'max_speed': 0.3}, 0.9)
    r1 = _call(*args, geofence=geofence, known=known, idle=idle, state=healthy_state)
    r2 = _call(*args, geofence=geofence, known=known, idle=idle, state=healthy_state)
    r3 = _call(*args, geofence=geofence, known=known, idle=idle, state=healthy_state)
    assert r1 == r2 == r3
