"""tier2_gate 내부 — 작은 기하 유틸 (M_dry, contradicts·state 공통).

3D 좌표를 받는 `tuple[float, float, float]` 컨벤션. 좌표계는 모듈 호출 컨텍스트에
따름 (ENU 또는 NED — 일관성은 호출자 책임).
"""

from __future__ import annotations

import math
from typing import Sequence


def l2(a: Sequence[float], b: Sequence[float]) -> float:
    """3D 유클리드 거리. 입력은 길이 3의 시퀀스 (tuple·list·numpy 모두 OK)."""
    return math.sqrt(
        (float(a[0]) - float(b[0])) ** 2
        + (float(a[1]) - float(b[1])) ** 2
        + (float(a[2]) - float(b[2])) ** 2
    )
