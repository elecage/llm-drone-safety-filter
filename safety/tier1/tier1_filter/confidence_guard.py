"""신뢰도 입력 위생 처리 — 비유한값(NaN/Inf) 복구 + 도메인 clamp.

filter_node 의 ``/intent/grounding_confidence`` 콜백 앞단에서 사용. ROS 의존성
없는 순수 함수로 분리해 host venv pytest 로 cover 한다 (filter_node 자체는
rclpy·px4_msgs 의존이라 host 단위 테스트 불가).

정책 (2026-06-12 세션 34 전체 리뷰 후속):
  - **비유한값 (NaN / ±Inf) → 0.0 (최대 마진 $r_\\text{max}$, fail-safe).**
    종전 코드의 ``max(0.0, min(1.0, c_raw))`` 단독은 Python 비교 의미론
    (NaN 비교가 항상 False) 때문에 ``min(1.0, nan)`` 이 1.0 을 반환 —
    비정상 입력이 *최대 신뢰도*(최소 마진)로 반전되는 fail-unsafe 경로였다.
  - **유한값 → $[0, 1]$ clamp** (종전과 동일, 방어적 도메인 검증).
  - *부재* (토픽 미수신)와 *비정상* (NaN/Inf)은 구분: 부재는 filter_node
    docstring 의 fail-active 정책($\\tilde c$ 초기값 1.0 = B1 동작) 유지,
    비정상은 상류가 깨졌다는 신호이므로 보수(0.0)로 복구.
"""

from __future__ import annotations

import math


def sanitize_confidence(c_raw: float) -> tuple[float, bool]:
    """신뢰도 raw 입력을 $[0, 1]$ 유한값으로 위생 처리.

    Args:
        c_raw: *의도해석기* 측 raw 신뢰도 (Float32 토픽 값).

    Returns:
        ``(c, finite)`` — ``c`` 는 $[0, 1]$ 보장값. ``finite`` 가 False 면
        입력이 NaN/Inf 여서 0.0(최대 마진)으로 복구했다는 뜻 (호출자 로깅용).
    """
    c = float(c_raw)
    if not math.isfinite(c):
        return 0.0, False
    return max(0.0, min(1.0, c)), True
