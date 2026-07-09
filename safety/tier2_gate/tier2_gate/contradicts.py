"""ADR-0019 D1·D3 — contradicts(σ'; σ_prev) 술어.

cmsm-proof §9.3 Φ_10 의 핵심 술어. 7 조건 (C1)–(C7)의 논리합.
in-progress 술어 (D3)는 mutual exclusive Enum (Activity) 으로 표현 — INSPECT 와
RETURN 는 *동시에* 진행될 수 없음을 타입 시스템에서 강제 (M1, PR #54 review).
실제 추적은 A4-2 state.py + gate_node.py (vehicle_local_position 토픽 기반).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from tier2_gate._geom import l2
from tier2_gate.thresholds import Thresholds


class Activity(str, Enum):
    """ADR-0019 D3 — Tier 1 상태 추적에서 결정론적으로 계산된 진행 상태.

    mutual exclusive — 드론은 한 시점에 idle/inspect/return 중 하나에만 위치.
    `INSPECT` = inspect-in-progress (viewpoint 도달 + tau_settle 안정 전).
    `RETURN`  = return-in-progress (도크 도달 전).
    """

    IDLE = 'idle'
    INSPECT = 'inspect'
    RETURN = 'return'


def contradicts(
    sigma_prev: str | None,
    theta_prev: Mapping[str, Any] | None,
    sigma_new: str,
    theta_new: Mapping[str, Any],
    *,
    activity: Activity,
    thresholds: Thresholds,
) -> bool:
    """ADR-0019 D1: 7 조건 (C1)–(C7)의 논리합.

    σ_prev가 None (세션 첫 명령)이면 항상 False — 모순할 대상 없음.
    ADR-0019 D2 매트릭스의 'normal' / 'Φ_2/Φ_3' / 'response flow' 칸은
    모두 False (게이트의 다른 cases가 처분).
    """
    if sigma_prev is None:
        return False
    assert theta_prev is not None, 'sigma_prev set but theta_prev missing'

    # (C1) 위치 변경 모순.
    if sigma_prev == 'move_to' and sigma_new == 'move_to':
        a = theta_prev.get('position')
        b = theta_new.get('position')
        if a is not None and b is not None and l2(a, b) > thresholds.D_cancel:
            return True

    # (C2) 이동 → 복귀.
    if sigma_prev == 'move_to' and sigma_new == 'return_to_dock':
        return True

    # (C3) 검사 → 이동 (inspect-in-progress).
    if (
        sigma_prev == 'inspect'
        and sigma_new == 'move_to'
        and activity == Activity.INSPECT
    ):
        return True

    # (C4) 검사 대상 변경 (inspect-in-progress + target 다름).
    if (
        sigma_prev == 'inspect'
        and sigma_new == 'inspect'
        and activity == Activity.INSPECT
    ):
        x = theta_prev.get('target_id')
        y = theta_new.get('target_id')
        if x != y:
            return True

    # (C5) 검사 → 복귀 (inspect-in-progress).
    if (
        sigma_prev == 'inspect'
        and sigma_new == 'return_to_dock'
        and activity == Activity.INSPECT
    ):
        return True

    # (C6) 복귀 → 이동 (return-in-progress).
    if (
        sigma_prev == 'return_to_dock'
        and sigma_new == 'move_to'
        and activity == Activity.RETURN
    ):
        return True

    # (C7) 복귀 → 검사 (return-in-progress).
    if (
        sigma_prev == 'return_to_dock'
        and sigma_new == 'inspect'
        and activity == Activity.RETURN
    ):
        return True

    return False
