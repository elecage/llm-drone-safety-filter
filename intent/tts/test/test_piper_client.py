"""piper_client 단위 테스트 — subprocess mock (실 Piper 무관, host venv)."""
from __future__ import annotations

from unittest import mock

import pytest

from intent_tts import piper_client as pc


class TestSanitize:
    def test_removes_shell_specials(self) -> None:
        assert pc._sanitize("위로 가$줘`x\\y") == "위로 가줘xy"

    def test_strips_whitespace(self) -> None:
        assert pc._sanitize("  안녕  ") == "안녕"


class TestSynthesize:
    def test_calls_piper_with_model_and_output(self) -> None:
        with mock.patch.object(pc.subprocess, "run") as m:
            pc.synthesize("왼쪽 머그컵?", "ko.onnx", "/tmp/o.wav")
        args = m.call_args
        cmd = args[0][0]
        assert cmd[0] == "piper"
        assert "--model" in cmd and "ko.onnx" in cmd
        assert "--output_file" in cmd and "/tmp/o.wav" in cmd
        assert args[1]["input"] == "왼쪽 머그컵?"

    def test_empty_text_noop(self) -> None:
        with mock.patch.object(pc.subprocess, "run") as m:
            pc.synthesize("   ", "ko.onnx", "/tmp/o.wav")
        m.assert_not_called()

    def test_custom_piper_bin(self) -> None:
        with mock.patch.object(pc.subprocess, "run") as m:
            pc.synthesize("x", "ko.onnx", "/tmp/o.wav", piper_bin="/opt/piper")
        assert m.call_args[0][0][0] == "/opt/piper"


class TestSpeak:
    def test_synthesize_then_play(self) -> None:
        calls = []
        with mock.patch.object(pc.subprocess, "run",
                               side_effect=lambda *a, **k: calls.append(a[0])):
            pc.speak("중앙 머그컵?", "ko.onnx")
        # 첫 호출 piper, 둘째 afplay
        assert calls[0][0] == "piper"
        assert calls[1][0] == "afplay"

    def test_empty_text_noop(self) -> None:
        with mock.patch.object(pc.subprocess, "run") as m:
            pc.speak("", "ko.onnx")
        m.assert_not_called()
