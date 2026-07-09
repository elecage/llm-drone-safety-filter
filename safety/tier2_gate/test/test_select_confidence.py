"""select_confidence 우선순위 단위 테스트.

게이트가 결정에 쓸 c 의 출처 우선순위 = estimator(_latest_c) > payload 'c' >
default. 세션 52 sim e2e 가 드러낸 결함(wrapper 가 σ payload 에 raw 'c' 를 항상
실어 estimator 의 합성 c 를 무력화)의 회귀 방지. gate_node 의 런타임 배선을 순수
함수로 추출해 host 에서 검증한다 — 종전엔 gate() 만 테스트해 이 결함을 못 잡았음.
"""

from __future__ import annotations

import pytest

from tier2_gate.gate import select_confidence


def test_estimator_c_overrides_payload_c():
    """estimator 가용 시 payload 'c' 무시 — ★ 세션 52 핵심 회귀."""
    # wrapper 가 raw c=1.0 을 실어도 estimator 합성 c=0.0 이 정본.
    assert select_confidence(0.0, {'c': 1.0}, 1.0) == 0.0
    assert select_confidence(0.42, {'c': 0.99}, 1.0) == 0.42


def test_payload_c_used_when_no_estimator():
    """estimator 부재(단독 운용·단위 test) → payload 'c' fallback."""
    assert select_confidence(None, {'c': 0.3}, 1.0) == 0.3


def test_default_when_neither():
    """estimator·payload 둘 다 없으면 default (첫 c 수신 전 startup)."""
    assert select_confidence(None, {}, 1.0) == 1.0
    assert select_confidence(None, {'sigma': 'inspect'}, 0.5) == 0.5


def test_estimator_zero_is_not_treated_as_absent():
    """_latest_c=0.0 은 '미수신(None)' 과 구분 — 0.0 도 유효한 정본 c."""
    assert select_confidence(0.0, {}, 1.0) == 0.0


def test_invalid_payload_c_raises():
    """payload 'c' 가 float 변환 불가면 예외 — 호출측 try 가 reject 처리."""
    with pytest.raises((ValueError, TypeError)):
        select_confidence(None, {'c': 'not-a-number'}, 1.0)


def test_returns_float():
    """반환은 항상 float."""
    assert isinstance(select_confidence(None, {'c': 1}, 1.0), float)
    assert isinstance(select_confidence(1, {}, 1.0), float)
