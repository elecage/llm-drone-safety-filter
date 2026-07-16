"""End-to-end pure-Python 통합 테스트 — gate + state + transition 시나리오.

ROS 2 smoke 는 A4-3 (sim 통합) 에서 검증. 본 테스트는 *gate_node 콜백 흐름*을
ROS 의존성 없이 GateSession + gate() 합성으로 재현해 M1·M2·M3 contract 가
실제 결정 흐름에서 일관되게 유지됨을 확인한다.
"""

from __future__ import annotations

import pytest

from tier2_gate.catalog import Geofence
from tier2_gate.contradicts import Activity
from tier2_gate.gate import Decision, gate
from tier2_gate.state import GateSession
from tier2_gate.thresholds import DEFAULT


@pytest.fixture
def geofence() -> Geofence:
    return Geofence(xmin=-3.0, xmax=3.0, ymin=-2.0, ymax=2.0, zmin=0.0, zmax=2.4)


@pytest.fixture
def known() -> frozenset:
    return frozenset({'sofa', 'mug', 'tv'})


def _run(session: GateSession, sigma, theta, c, *, geofence, known):
    """gate_node 의 _on_command 핵심 흐름 시뮬레이션 — 결정 + transition."""
    gs = session.to_gate_state()
    result = gate(
        sigma, theta, c,
        sigma_prev=session.sigma_prev,
        theta_prev=session.theta_prev,
        activity=session.activity,
        geofence=geofence, known_objects=known,
        state=gs, thresholds=DEFAULT,
    )
    if result.decision == Decision.ACCEPT:
        session.on_accept(sigma, theta)
    elif result.decision == Decision.CONFIRM:
        session.on_confirm()
    return result


# ---- 시나리오 1 — 정상 inspect 흐름 ----

def test_e2e_inspect_then_idle_after_settle(geofence, known):
    """ACCEPT inspect → 위치 도달 → settle → IDLE → 새 ACCEPT 정상."""
    s = GateSession(target_poses={'sofa': (1.0, 0.0, 1.0)})

    r1 = _run(s, 'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}, 0.9,
              geofence=geofence, known=known)
    assert r1.decision == Decision.ACCEPT
    assert s.activity == Activity.INSPECT

    # 드론 viewpoint 도달
    s.drone_pos_enu = (1.0, 0.0, 1.0)
    s.update_activity_progress(thresholds=DEFAULT, now=100.0)
    s.update_activity_progress(thresholds=DEFAULT, now=101.5)  # tau_settle 경과
    assert s.activity == Activity.IDLE

    # 다음 inspect 정상 ACCEPT (이전 inspect 완료라 contradicts 안 발동)
    r2 = _run(s, 'inspect', {'target_id': 'mug', 'viewpoint': 'close'}, 0.9,
              geofence=geofence, known=known)
    assert r2.decision == Decision.ACCEPT


# ---- 시나리오 2 — inspect 중 모순 명령 → confirm 강제 ----

def test_e2e_inspect_then_move_during_progress_forces_confirm(geofence, known):
    s = GateSession(target_poses={'sofa': (1.0, 0.0, 1.0)})
    _run(s, 'inspect', {'target_id': 'sofa', 'viewpoint': 'overview'}, 0.9,
         geofence=geofence, known=known)
    assert s.activity == Activity.INSPECT

    # inspect 진행 중 move_to → C3 contradicts → confirm
    r = _run(s, 'move_to', {'target_id': 'sofa'}, 0.9,
             geofence=geofence, known=known)
    assert r.decision == Decision.CONFIRM
    assert 'Φ_10' in r.violations
    assert s.confirm_fired_at is not None  # M2 timer 시작


# ---- 시나리오 3 — confirm timeout → reject ----

def test_e2e_confirm_timeout_blocks_next_command(geofence, known, monkeypatch):
    """pytest monkeypatch 로 `time.monotonic` 안전 패치 (자동 복구·race-free)."""
    import time as _time

    s = GateSession()

    # 첫 confirm 발동 (return_to_dock 인 confirm band).
    _run(s, 'return_to_dock', {}, 0.5,
         geofence=geofence, known=known)
    # confirm_fired_at 임의 시각으로 set, T_resp 경과 시뮬.
    s.confirm_fired_at = 0.0
    monkeypatch.setattr(_time, 'monotonic', lambda: DEFAULT.T_resp + 1.0)

    gs = s.to_gate_state()
    assert gs.confirm_pending_elapsed_s == pytest.approx(DEFAULT.T_resp + 1.0)
    # 다음 명령 — Φ_9 violation → reject.
    r = _run(s, 'move_to', {'target_id': 'sofa'}, 0.9,
             geofence=geofence, known=known)
    assert r.decision == Decision.REJECT
    assert 'Φ_9' in r.violations


# ---- 시나리오 4 — n_sc 누적 → reject, ACCEPT 후 reset ----

def test_e2e_n_sc_accumulates_then_resets_at_accept(geofence, known):
    s = GateSession()

    # self-correction 3회 누적 → Φ_8 위반
    s.on_self_correction()
    s.on_self_correction()
    s.on_self_correction()
    r1 = _run(s, 'move_to', {'target_id': 'sofa'}, 0.9,
              geofence=geofence, known=known)
    assert r1.decision == Decision.REJECT
    assert 'Φ_8' in r1.violations

    # n_sc 를 줄여서 (이후 ACCEPT 후 M3 reset 검증을 위해) 일단 한 번 ACCEPT.
    s.n_sc = 0
    r2 = _run(s, 'move_to', {'target_id': 'sofa'}, 0.9,
              geofence=geofence, known=known)
    assert r2.decision == Decision.ACCEPT
    assert s.n_sc == 0  # ACCEPT 직후 M3 reset

    # 새 self-correction 사이클 — 누적 가능
    s.on_self_correction()
    assert s.n_sc == 1


# ---- 시나리오 5 — ask_user 응답 후 user_confirmed 로 return_to_dock 정상 ----

def test_e2e_ask_user_response_unlocks_return(geofence, known):
    s = GateSession()

    # return_to_dock 첫 시도 — Φ_3 reject (user_confirmed=False)
    r1 = _run(s, 'return_to_dock', {}, 0.9,
              geofence=geofence, known=known)
    assert r1.decision == Decision.REJECT
    assert 'Φ_3' in r1.violations

    # ask_user 응답 도착
    s.on_user_response(True)
    assert s.user_confirmed is True

    # 재시도 — Φ_3 통과, ACCEPT
    r2 = _run(s, 'return_to_dock', {}, 0.9,
              geofence=geofence, known=known)
    assert r2.decision == Decision.ACCEPT
    assert s.activity == Activity.RETURN
    # ACCEPT 후 user_confirmed 단발성 reset (M3 boundary 정합)
    assert s.user_confirmed is False
