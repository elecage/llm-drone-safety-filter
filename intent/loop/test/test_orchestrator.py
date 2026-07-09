"""orchestrator 단위 테스트 — IO 주입 mock 루프."""
from __future__ import annotations

from intent_loop.loop_policy import LoopConfig, LoopDecision
from intent_loop.orchestrator import LoopIO, run_clarification_loop


class _Clock:
    """주입 시계 — tick 마다 step 초 증가."""
    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step
    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def _io(utterances, intents, clock_step=1.0):
    """utterances: capture 순서 / intents: receive_intent 순서 [(is_ask, q)]."""
    spoken, hovered, published = [], [], []
    uit, iit = iter(utterances), iter(intents)
    io = LoopIO(
        capture_utterance=lambda: next(uit),
        publish_intent_input=lambda s: published.append(s),
        receive_intent=lambda: next(iit),
        speak=lambda q: spoken.append(q),
        hover=lambda: hovered.append(True),
        now=_Clock(clock_step),
    )
    return io, spoken, hovered, published


class TestRunLoop:
    def test_clear_first_turn_executes(self) -> None:
        io, spoken, hovered, published = _io(
            ["TV로 가줘"], [(False, "")],
        )
        r = run_clarification_loop(io)
        assert r.outcome is LoopDecision.EXECUTE
        assert r.turns == 0
        assert spoken == [] and hovered == []
        assert published == ["TV로 가줘"]

    def test_execute_with_confirmation_speaks(self) -> None:
        # EXECUTE 시 확인 문구(비어있지 않음) → 실행 직전 수락 피드백 speak.
        io, spoken, hovered, published = _io(
            ["소파로 가줘"], [(False, "「소파로 가줘」 알겠어요, 이동할게요.")],
        )
        r = run_clarification_loop(io)
        assert r.outcome is LoopDecision.EXECUTE
        assert r.turns == 0
        assert spoken == ["「소파로 가줘」 알겠어요, 이동할게요."]
        assert hovered == []
        assert published == ["소파로 가줘"]

    def test_one_clarify_then_execute(self) -> None:
        # 1차 모호(ask_user) → 질의 → 응답 → 2차 명확 → EXECUTE
        io, spoken, hovered, published = _io(
            ["머그컵 보여줘", "왼쪽"],
            [(True, "왼쪽 중앙 오른쪽 중 어느 것?"), (False, "")],
        )
        r = run_clarification_loop(io)
        assert r.outcome is LoopDecision.EXECUTE
        assert r.turns == 1
        assert spoken == ["왼쪽 중앙 오른쪽 중 어느 것?"]
        assert hovered == []
        # 누적: 1차 "머그컵 보여줘" → 2차 "머그컵 보여줘 왼쪽"
        assert published == ["머그컵 보여줘", "머그컵 보여줘 왼쪽"]
        assert r.transcript == ["머그컵 보여줘", "왼쪽"]

    def test_max_turns_hover(self) -> None:
        # 계속 모호 → max_turns(3) 초과 → TIMEOUT_HOVER
        io, spoken, hovered, published = _io(
            ["그거", "저거", "음", "글쎄"],
            [(True, "q1"), (True, "q2"), (True, "q3"), (True, "q4")],
            clock_step=0.1,  # timeout 전에 turn 한도 도달
        )
        r = run_clarification_loop(io, LoopConfig(max_turns=3, timeout_s=100.0))
        assert r.outcome is LoopDecision.TIMEOUT_HOVER
        assert r.turns == 3
        assert hovered == [True]

    def test_timeout_hover(self) -> None:
        # 시계가 빨리 흘러 timeout 먼저 → TIMEOUT_HOVER
        io, spoken, hovered, published = _io(
            ["그거", "저거"],
            [(True, "q1"), (True, "q2")],
            clock_step=20.0,  # 2번째 receive 시 elapsed≥30
        )
        r = run_clarification_loop(io, LoopConfig(max_turns=9, timeout_s=30.0))
        assert r.outcome is LoopDecision.TIMEOUT_HOVER
        assert hovered == [True]
