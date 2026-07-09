"""macOS `say` TTS 클라이언트 — 한국어 ask_user 음성 출력 (ADR-0016 D2 amendment).

[piper_client](piper_client.py) 가 한국어 voice 미지원(Piper voices 저장소에 ko_KR
없음, 2026-05-29 확인)이라, **한국어 명료화 질문은 macOS 내장 `say`** 로 출력.
say 는 ko_KR voice 다수(Yuna/Sora/Jian 등) + 내장(설치 불필요) + CLI(데몬 불필요)
+ 직접 스피커 출력(afplay 불필요). paper-1 sim 호스트 = macOS([ADR-0008](../../../docs/handover/decisions/0008-paper1-gui-path-native-macos.md))
라 항상 가용.

언어 선택은 STT 와 공통의 ``VOICE_LANG`` (ko/en/auto) 으로 결정하고, 호출 측
([clarification_loop](../../../scripts/clarification_loop.py)) 이 [voice_for_lang]
으로 voice 를 구해 인자로 넘긴다. ``VOICE_LANG=auto`` 면 voice="auto" 가 되어
매 발화 텍스트의 한글 포함 여부로 [pick_voice] 가 ko/en voice 를 고른다.

Piper 는 cross-platform(ONNX) 영어/다언어 옵션으로 유지([piper_client](piper_client.py)).
backend 선택은 [tts_pipeline](../../../scripts/tts_pipeline.py) 의 TTS_BACKEND.
"""
from __future__ import annotations

import os
import re
import subprocess

# ask_user 질문에 불필요한 쉘/제어 특수문자 제거 (piper_client _sanitize 정합).
# subprocess 리스트 호출이라 쉘 주입은 없지만 백틱·달러 등 잡음 제거.
_UNSAFE = re.compile(r'[`\\$]')

# 한글 syllables (U+AC00–U+D7A3) + jamo (U+1100–U+11FF) + 호환 jamo (U+3130–U+318F).
# 하나라도 포함되면 한국어 텍스트로 간주.
_HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")

# 한국어 voice 선호 순위 — 같은 Yuna 라도 Premium > Enhanced > 기본(compact)
# 순으로 자연스럽다. 설치된 것 중 최상위를 고른다(미설치 환경은 기본 Yuna fallback).
# SAY_VOICE_KO env 로 고정 가능(예: 다른 화자 Suhyun).
_KO_VOICE_PREFERENCE = ("Yuna (Premium)", "Yuna (Enhanced)", "Yuna")
_VOICE_RE = re.compile(r"^(.+?)\s{2,}[a-z]{2}_[A-Z]{2}")


def _installed_voices() -> set:
    """`say -v '?'` 의 설치된 voice 이름 집합 (실패 시 빈 집합)."""
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in out.splitlines():
        m = _VOICE_RE.match(line)
        if m:
            names.add(m.group(1).strip())
    return names


def _resolve_ko_voice() -> str:
    """SAY_VOICE_KO env > 설치된 것 중 선호 순위 최상위 > 기본 Yuna."""
    env = (os.environ.get("SAY_VOICE_KO") or "").strip()
    if env:
        return env
    installed = _installed_voices()
    for v in _KO_VOICE_PREFERENCE:
        if v in installed:
            return v
    return "Yuna"


DEFAULT_VOICE_KO = _resolve_ko_voice()  # Premium 설치 시 자동 선택 (자연스러움 ↑)
DEFAULT_VOICE_EN = "Samantha"  # en_US (macOS 기본 영어 voice)
AUTO_VOICE = "auto"            # voice 인자 sentinel — 텍스트 언어로 자동 선택
DEFAULT_VOICE = AUTO_VOICE
DEFAULT_SAY_BIN = "say"


def _sanitize(text: str) -> str:
    return _UNSAFE.sub("", text).strip()


def pick_voice(
    text: str,
    voice_ko: str = DEFAULT_VOICE_KO,
    voice_en: str = DEFAULT_VOICE_EN,
) -> str:
    """텍스트의 한글 포함 여부로 ko/en voice 선택 (auto 모드용)."""
    return voice_ko if _HANGUL_RE.search(text) else voice_en


def voice_for_lang(lang: str) -> str:
    """STT·TTS 공통 언어 코드(`VOICE_LANG`) → say voice.

    - ``ko``   → Yuna   (한국어 voice 고정)
    - ``en``   → Samantha (영어 voice 고정)
    - 그 외(``auto`` 등) → ``"auto"`` 센티넬 (매 발화 텍스트로 [pick_voice])
    """
    key = (lang or "").lower()
    if key == "ko":
        return DEFAULT_VOICE_KO
    if key == "en":
        return DEFAULT_VOICE_EN
    return AUTO_VOICE


def _resolve_voice(text: str, voice: str) -> str:
    if not voice or voice == AUTO_VOICE:
        return pick_voice(text)
    return voice


def synthesize(
    text: str,
    out_path: str,
    voice: str = DEFAULT_VOICE,
    say_bin: str = DEFAULT_SAY_BIN,
    timeout: float = 30.0,
) -> None:
    """텍스트를 `say` 로 합성해 ``out_path`` (aiff) 파일로 저장. 빈 텍스트 no-op.

    ``voice='auto'`` 면 텍스트 언어로 자동 선택 ([pick_voice]).
    """
    safe = _sanitize(text)
    if not safe:
        return
    resolved = _resolve_voice(safe, voice)
    subprocess.run(
        [say_bin, "-v", resolved, "-o", out_path, safe],
        check=True,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def speak(
    text: str,
    voice: str = DEFAULT_VOICE,
    say_bin: str = DEFAULT_SAY_BIN,
    timeout: float = 30.0,
) -> None:
    """텍스트를 `say` 로 즉시 스피커 출력 (파일 불필요). 빈 텍스트 no-op.

    ``voice='auto'`` 면 텍스트 언어로 자동 선택 ([pick_voice]).
    """
    safe = _sanitize(text)
    if not safe:
        return
    resolved = _resolve_voice(safe, voice)
    subprocess.run(
        [say_bin, "-v", resolved, safe],
        check=True,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
