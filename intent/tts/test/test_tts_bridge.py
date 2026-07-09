"""tts_bridge 단위 테스트 — echo 파싱 (subprocess 무관, host venv)."""
from __future__ import annotations

from intent_tts import tts_bridge as tb


class TestParseQuestion:
    def test_single_quoted(self) -> None:
        assert tb.parse_question("'왼쪽 머그컵?'") == "왼쪽 머그컵?"

    def test_double_quoted(self) -> None:
        assert tb.parse_question('"중앙?"') == "중앙?"

    def test_unquoted(self) -> None:
        assert tb.parse_question("오른쪽") == "오른쪽"


class TestIterQuestionsFromEcho:
    def test_extracts_data_lines(self) -> None:
        lines = [
            "data: '식탁 위 머그컵 셋 중 어느 것?'",
            "---",
            "data: '왼쪽인가요?'",
            "---",
        ]
        assert list(tb.iter_questions_from_echo(iter(lines))) == [
            "식탁 위 머그컵 셋 중 어느 것?", "왼쪽인가요?",
        ]

    def test_skips_non_data_lines(self) -> None:
        lines = ["---", "  ", "data: '질문'", "garbage"]
        assert list(tb.iter_questions_from_echo(iter(lines))) == ["질문"]

    def test_skips_empty_question(self) -> None:
        lines = ["data: ''", "data: '진짜질문'"]
        assert list(tb.iter_questions_from_echo(iter(lines))) == ["진짜질문"]


class TestStreamQuestions:
    def test_calls_callback_per_question(self) -> None:
        class _FakeProc:
            stdout = iter(["data: 'q1'", "---", "data: 'q2'"])
            def terminate(self): pass
        received = []
        tb.stream_questions(
            received.append, container="c",
            _popen=lambda *a, **k: _FakeProc(),
        )
        assert received == ["q1", "q2"]


class TestFirstQuestion:
    def test_returns_first(self) -> None:
        class _R:
            stdout = "data: 'first?'\n---\n"
        assert tb.first_question(_run=lambda *a, **k: _R()) == "first?"

    def test_none_when_empty(self) -> None:
        class _R:
            stdout = ""
        assert tb.first_question(_run=lambda *a, **k: _R()) is None
