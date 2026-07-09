"""loop_policy 단위 테스트 — Φ_9/L4 종료 안전."""
from __future__ import annotations

import pytest

from intent_loop.loop_policy import LoopConfig, LoopDecision, decide


class TestLoopConfig:
    def test_defaults(self) -> None:
        c = LoopConfig()
        assert c.max_turns == 3
        assert c.timeout_s == 30.0

    def test_bad_turns(self) -> None:
        with pytest.raises(ValueError):
            LoopConfig(max_turns=0)

    def test_bad_timeout(self) -> None:
        with pytest.raises(ValueError):
            LoopConfig(timeout_s=0)


class TestDecide:
    def test_clear_intent_executes(self) -> None:
        # ask_user 아님 → 한도 무관 EXECUTE
        assert decide(False, 0, 0.0) is LoopDecision.EXECUTE
        assert decide(False, 99, 999.0) is LoopDecision.EXECUTE

    def test_ambiguous_within_limit_clarifies(self) -> None:
        assert decide(True, 0, 0.0) is LoopDecision.CLARIFY
        assert decide(True, 2, 29.0) is LoopDecision.CLARIFY

    def test_turn_limit_hovers(self) -> None:
        # turn >= max_turns(3) → TIMEOUT_HOVER
        assert decide(True, 3, 0.0) is LoopDecision.TIMEOUT_HOVER

    def test_timeout_hovers(self) -> None:
        # elapsed >= timeout_s(30) → TIMEOUT_HOVER
        assert decide(True, 0, 30.0) is LoopDecision.TIMEOUT_HOVER
        assert decide(True, 1, 45.0) is LoopDecision.TIMEOUT_HOVER

    def test_safety_floor_never_clarify_past_limit(self) -> None:
        # L4: 한도 초과 시 절대 CLARIFY 안 함 (무한 루프 방지)
        cfg = LoopConfig(max_turns=2, timeout_s=10.0)
        for turn in range(2, 10):
            assert decide(True, turn, 0.0, cfg) is LoopDecision.TIMEOUT_HOVER
        for el in (10.0, 20.0, 100.0):
            assert decide(True, 0, el, cfg) is LoopDecision.TIMEOUT_HOVER

    def test_clear_intent_overrides_timeout(self) -> None:
        # 명확하면 timeout 초과해도 EXECUTE (의도는 유효, Tier1/2가 검증)
        assert decide(False, 5, 100.0) is LoopDecision.EXECUTE
