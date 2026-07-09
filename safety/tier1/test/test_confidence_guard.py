"""Unit test for confidence_guard.py — 비유한값 복구 + 도메인 clamp.

2026-06-12 세션 34 전체 리뷰 후속: 종전 ``max(0.0, min(1.0, c_raw))`` 단독이
NaN 을 1.0(최대 신뢰도 = 최소 마진)으로 반전시키는 fail-unsafe 경로 회귀 방지.
"""

from __future__ import annotations

import math

import pytest

from tier1_filter.confidence_guard import sanitize_confidence


# ==================================================================
# 비유한값 — 0.0 (최대 마진) 복구 + finite=False
# ==================================================================

@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_recovers_to_zero(bad):
    c, finite = sanitize_confidence(bad)
    assert c == 0.0
    assert finite is False


def test_nan_does_not_become_max_confidence():
    """회귀 고정: 종전 버그는 min(1.0, nan)=1.0 → NaN 이 최대 신뢰도로 반전."""
    legacy = max(0.0, min(1.0, float('nan')))  # 종전 코드 경로 재현.
    assert legacy == 1.0  # 버그 전제 확인 (Python 비교 의미론).
    c, finite = sanitize_confidence(float('nan'))
    assert c == 0.0 and finite is False  # 수정 후: 보수 방향.


# ==================================================================
# 유한값 — [0, 1] clamp + finite=True
# ==================================================================

@pytest.mark.parametrize('raw, expected', [
    (0.0, 0.0),
    (1.0, 1.0),
    (0.37, 0.37),
    (-0.5, 0.0),       # 하향 일탈 → 0.0
    (1.5, 1.0),        # 상향 일탈 → 1.0
    (-1e308, 0.0),     # 극단 유한값
    (1e308, 1.0),
])
def test_finite_clamped_to_unit_interval(raw, expected):
    c, finite = sanitize_confidence(raw)
    assert c == expected
    assert finite is True


def test_int_input_accepted():
    """Float32 변환 전 정수 입력도 안전 처리 (방어적)."""
    c, finite = sanitize_confidence(1)
    assert c == 1.0 and finite is True


def test_output_always_in_domain():
    """출력 불변식: 입력이 무엇이든 c ∈ [0, 1] ∧ math.isfinite(c)."""
    for raw in [float('nan'), float('inf'), float('-inf'),
                -2.0, -0.0, 0.5, 2.0, 1e-12, 1.0 + 1e-12]:
        c, _ = sanitize_confidence(raw)
        assert 0.0 <= c <= 1.0
        assert math.isfinite(c)
