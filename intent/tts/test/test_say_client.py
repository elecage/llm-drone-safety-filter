"""say_client 단위 테스트 — subprocess mock (실 say 무관, host venv)."""
from __future__ import annotations

from unittest import mock

from intent_tts import say_client as sc


class TestSanitize:
    def test_removes_shell_specials(self) -> None:
        assert sc._sanitize("왼쪽`$\\ 머그컵") == "왼쪽 머그컵"

    def test_strips(self) -> None:
        assert sc._sanitize("  안녕  ") == "안녕"


class TestSynthesize:
    def test_say_command(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.synthesize("왼쪽?", "/tmp/o.aiff", voice="Yuna")
        cmd = m.call_args[0][0]
        assert cmd[0] == "say"
        assert "-v" in cmd and "Yuna" in cmd
        assert "-o" in cmd and "/tmp/o.aiff" in cmd
        assert "왼쪽?" in cmd

    def test_empty_noop(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.synthesize("  ", "/tmp/o.aiff")
        m.assert_not_called()


class TestSpeak:
    def test_say_direct_no_output_file(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.speak("중앙 머그컵인가요?", voice="Sora")
        cmd = m.call_args[0][0]
        assert cmd[0] == "say"
        assert "Sora" in cmd
        assert "-o" not in cmd  # speak 은 파일 없이 직접 스피커
        assert "중앙 머그컵인가요?" in cmd

    def test_default_voice_yuna(self) -> None:
        # 한글 → 한국어 기본 voice (DEFAULT_VOICE_KO). Premium 설치 환경에선
        # "Yuna (Premium)" 처럼 variant 가 선택되므로 정확 매칭 대신 값 참조.
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.speak("질문")
        assert sc.DEFAULT_VOICE_KO in m.call_args[0][0]

    def test_empty_noop(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.speak("")
        m.assert_not_called()

    def test_auto_picks_english_for_english_text(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.speak("Which mug do you mean?")
        cmd = m.call_args[0][0]
        assert "Samantha" in cmd
        assert "Yuna" not in cmd

    def test_auto_picks_korean_for_mixed_text(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.speak("OK 왼쪽 mug?")
        assert sc.DEFAULT_VOICE_KO in m.call_args[0][0]

    def test_explicit_voice_overrides_auto(self) -> None:
        with mock.patch.object(sc.subprocess, "run") as m:
            sc.speak("Which mug?", voice="Yuna")
        assert "Yuna" in m.call_args[0][0]


class TestPickVoice:
    def test_hangul_returns_korean_voice(self) -> None:
        assert sc.pick_voice("왼쪽이요") == sc.DEFAULT_VOICE_KO

    def test_ascii_returns_english_voice(self) -> None:
        assert sc.pick_voice("left one") == sc.DEFAULT_VOICE_EN

    def test_jamo_detected_as_korean(self) -> None:
        # 자모 단독 ("ㄱ" 같은 U+3131 호환 jamo) 도 한국어로.
        assert sc.pick_voice("ㄱㄴㄷ") == sc.DEFAULT_VOICE_KO


class TestVoiceForLang:
    def test_ko_fixes_korean_voice(self) -> None:
        assert sc.voice_for_lang("ko") == sc.DEFAULT_VOICE_KO

    def test_en_fixes_english_voice(self) -> None:
        assert sc.voice_for_lang("en") == sc.DEFAULT_VOICE_EN

    def test_auto_returns_auto_sentinel(self) -> None:
        assert sc.voice_for_lang("auto") == sc.AUTO_VOICE

    def test_uppercase_normalized(self) -> None:
        assert sc.voice_for_lang("KO") == sc.DEFAULT_VOICE_KO
        assert sc.voice_for_lang("EN") == sc.DEFAULT_VOICE_EN

    def test_empty_falls_back_to_auto(self) -> None:
        assert sc.voice_for_lang("") == sc.AUTO_VOICE
        assert sc.voice_for_lang("ja") == sc.AUTO_VOICE  # 미지원 → auto
