"""ADR-0019 D1·D3 — contradicts(σ'; σ_prev) 술어.

cmsm-proof §9.3 Φ_10 의 핵심 술어. 7 조건 (C1)–(C7)의 논리합.
in-progress 술어 (D3)는 mutual exclusive Enum (Activity) 으로 표현 — INSPECT 와
RETURN 는 *동시에* 진행될 수 없음을 타입 시스템에서 강제 (M1, PR #54 review).
실제 추적은 A4-2 state.py + gate_node.py (vehicle_local_position 토픽 기반).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from tier2_gate.thresholds import Thresholds

_OPPOSITE_DIRECTION: Mapping[str, str] = {
    'forward': 'back', 'back': 'forward',
    'left': 'right', 'right': 'left',
    'up': 'down', 'down': 'up',
}
"""ADR-0049 D6 — C1 의미 인자 판의 방향 반전 쌍 (catalog.MOVE_TO_DIRECTIONS 동기)."""


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

    # (C1) 이동 목적지 변경 모순 — ADR-0049 D6 의미 인자 판.
    # 종전 position 거리(D_cancel) 판정은 ADR-0049 D1(게이트 좌표 거부)로
    # 도달 불가 → 목적지 반전의 의미 판정으로 대체: 명명 대상 변경 또는
    # 방향 반전(forward↔back 등). 혼합 형태(target↔direction)는 비교 기하가
    # 없어 모순으로 보지 않음(정제 명령으로 간주).
    if sigma_prev == 'move_to' and sigma_new == 'move_to':
        t_prev = theta_prev.get('target_id')
        t_new = theta_new.get('target_id')
        if t_prev is not None and t_new is not None and t_prev != t_new:
            return True
        d_prev = theta_prev.get('direction')
        d_new = theta_new.get('direction')
        if (
            d_prev is not None
            and d_new is not None
            and _OPPOSITE_DIRECTION.get(str(d_prev)) == d_new
        ):
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
