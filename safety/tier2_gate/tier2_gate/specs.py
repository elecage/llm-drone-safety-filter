"""cmsm-proof §9.3 / ADR-0013 D3 — LTL 사양 Φ_1 … Φ_10 의 게이트 측 평가.

각 `check_phi_N` 함수는 *디스패치 시점에 Φ_N 위반인가*를 평가. Φ_4 (신뢰도 하한)·
Φ_10 (모순) 는 게이트 결정 함수의 case 2·case 4에서 직접 처분되므로 `violations()`
결과에 포함되지 않음 — cmsm-proof §9.4 참조.

paper-1 narrow 해석 — Φ_3: ask_user (action_class='confirm') 는 게이트 *내부*
응답이라 user_confirmed 강제 면제 (외부 dispatch 의 무한회귀 회피).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tier2_gate.catalog import SKILL_ACTION_CLASS, ActionClass, Geofence
from tier2_gate.thresholds import Thresholds


@dataclass(frozen=True)
class GateState:
    """게이트 평가 시점의 시스템·세션 상태.

    `user_confirmed`: 이 dispatch 가 직전 ask_user 응답으로 승인됨.
    `n_sc`: 명령 세션 시작 이래 사용자 자기수정 누적 카운트 (Φ_8).
    `confirm_pending_elapsed_s`: confirm 발동 후 응답 도달까지 경과 시간 [s].
        None 이면 confirm 보류 중 아님.
    """

    battery_pct: float = 100.0
    link_lost: bool = False
    tier1_active: bool = True
    user_confirmed: bool = False
    n_sc: int = 0
    confirm_pending_elapsed_s: float | None = None


def check_phi_1(
    sigma: str,
    theta: Mapping[str, Any],
    *,
    geofence: Geofence,
) -> bool:
    """Φ_1: □ ¬outside-geofence — move_to 목표가 지오펜스 밖이면 위반.

    move_to 외 스킬은 Tier 1 안전 필터가 지오펜스 enforcement.

    ⚠️ REDUNDANT (dead code) — CC-2 (catalog.py, move_to position ∈ geofence)
    가 게이트 결정 함수 case 1 에서 먼저 단락하므로, Φ 평가 (case 2–3) 가
    도달할 무렵엔 geofence-밖 move_to 가 이미 거부됨 → check_phi_1 은 실효 도달
    불가. C38 (2026-06-27 세션 56) 에서 cross-backbone 실측으로 확인 (geofence-밖
    move_to 292 건 전부 CC-2 로 거부, Φ_1 경로 미도달). Φ_2 (CC-1 동치) 도 동일
    구조. 제거 검토는 ROADMAP C42 (구 C39). 본 함수는 정형 (cmsm-proof §9.3) 과의
    1:1 대응 보존을 위해 유지.

    ADR-0049 이후 이중 사문화 — D1 이 position 을 스키마에서 범주 거부하므로
    본 검사의 position 분기는 게이트 흐름에서 원리적으로 도달 불가. 해소
    좌표의 지오펜스는 운용 가드(sigma_bridge)·티어 0 담당 (ADR-0049 D3).
    """
    if sigma != 'move_to':
        return False
    pos = theta.get('position')
    if pos is None or len(pos) != 3:
        return False  # CC-2 가 이미 잡음.
    try:
        position = (float(pos[0]), float(pos[1]), float(pos[2]))
    except (TypeError, ValueError):
        return False
    return not geofence.contains(position)


def check_phi_2(sigma: str) -> bool:
    """Φ_2: action-class ∈ {monitoring, return, confirm} — CATALOG 외 거부 (CC-1 동치)."""
    return sigma not in SKILL_ACTION_CLASS


def check_phi_3(sigma: str, state: 'GateState') -> bool:
    """Φ_3: 비-monitoring 동작은 사용자 확인 필요.

    paper-1 narrow 해석 — 'return' 클래스만 user_confirmed 강제.
    ask_user 는 게이트 내부 응답이므로 면제 ('confirm' 클래스).
    """
    ac = SKILL_ACTION_CLASS.get(sigma)
    return ac == ActionClass.RETURN and not state.user_confirmed


def check_phi_5(sigma: str, *, state: 'GateState', thresholds: Thresholds) -> bool:
    """Φ_5: battery < B_rtl ⇒ return — 디스패치 시점에 return/emergency 외는 거부."""
    return (
        state.battery_pct < thresholds.B_rtl
        and sigma not in ('return_to_dock', 'emergency_land')
    )


def check_phi_6(sigma: str, *, state: 'GateState') -> bool:
    """Φ_6: link-loss ⇒ emergency-land — link 손실 상태에서 emergency 외 거부."""
    return state.link_lost and sigma != 'emergency_land'


def check_phi_7(state: 'GateState') -> bool:
    """Φ_7: □ tier1-active — Tier 1 비활성이면 게이트는 어떤 dispatch 도 못함."""
    return not state.tier1_active


def check_phi_8(*, state: 'GateState', thresholds: Thresholds) -> bool:
    """Φ_8: 자기수정 누적이 임계 이상이면 위반 (인지 단절)."""
    return state.n_sc >= thresholds.N_sc


def check_phi_9(*, state: 'GateState', thresholds: Thresholds) -> bool:
    """Φ_9: confirm 발동 후 T_resp 안에 사용자 응답 — 초과 시 위반."""
    elapsed = state.confirm_pending_elapsed_s
    return elapsed is not None and elapsed > thresholds.T_resp


def violations(
    sigma: str,
    theta: Mapping[str, Any],
    *,
    geofence: Geofence,
    state: GateState,
    thresholds: Thresholds,
) -> list[str]:
    """디스패치 시 위반되는 Φ_i 식별자 리스트.

    cmsm-proof §9.4 — Φ_4 (c < c_lo) · Φ_10 (contradicts) 는 게이트 결정 함수의
    별 case 에서 처분되므로 본 결과에서 제외.
    """
    out: list[str] = []
    if check_phi_1(sigma, theta, geofence=geofence):
        out.append('Φ_1')
    if check_phi_2(sigma):
        out.append('Φ_2')
    if check_phi_3(sigma, state):
        out.append('Φ_3')
    if check_phi_5(sigma, state=state, thresholds=thresholds):
        out.append('Φ_5')
    if check_phi_6(sigma, state=state):
        out.append('Φ_6')
    if check_phi_7(state):
        out.append('Φ_7')
    if check_phi_8(state=state, thresholds=thresholds):
        out.append('Φ_8')
    if check_phi_9(state=state, thresholds=thresholds):
        out.append('Φ_9')
    return out
