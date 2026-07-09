"""Piper TTS 클라이언트 — 텍스트 → 음성 합성 + 재생 (ADR-0016 D2).

STT 의 [whisper_client](../../stt/intent_stt/whisper_client.py) 대칭 — 역방향
(텍스트 → 음성). Piper CLI subprocess 로 wav 합성 후 macOS `afplay` 로 재생.

## ADR-0016 D2 정합

- 모델: Piper TTS (Rhasspy, MIT). voice = ko_KR-* (ask_user 질문이 한국어).
  영어 시나리오 시 en_US-lessac-medium.
- backend: ONNX runtime (host macOS). IPC = CLI subprocess (ADR-0016 D2 의
  "REST 또는 Unix socket 별 결정" 을 *CLI subprocess* 로 잠금 — whisper-server
  HTTP 와 달리 Piper 는 CLI 가 표준이며 데몬 불필요).
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

# ask_user 질문에 불필요한 쉘/제어 특수문자 제거 (STT _sanitize 대칭).
_UNSAFE = re.compile(r'[`\\$]')

DEFAULT_PIPER_BIN = "piper"
DEFAULT_PLAYER = "afplay"  # macOS 기본 오디오 재생


def _sanitize(text: str) -> str:
    return _UNSAFE.sub("", text).strip()


def synthesize(
    text: str,
    model_path: str,
    out_path: str,
    piper_bin: str = DEFAULT_PIPER_BIN,
    timeout: float = 30.0,
) -> None:
    """텍스트를 Piper 로 합성해 ``out_path`` wav 파일로 저장.

    Piper CLI: stdin 텍스트 → ``--output_file`` wav. 빈 텍스트는 no-op.
    Piper 미설치 / 합성 오류 → subprocess.CalledProcessError 전파.
    """
    safe = _sanitize(text)
    if not safe:
        return
    subprocess.run(
        [piper_bin, "--model", model_path, "--output_file", out_path],
        input=safe,
        text=True,
        check=True,
        timeout=timeout,
        capture_output=True,
    )


def speak(
    text: str,
    model_path: str,
    piper_bin: str = DEFAULT_PIPER_BIN,
    player: str = DEFAULT_PLAYER,
    timeout: float = 30.0,
) -> None:
    """텍스트를 합성 후 즉시 재생 (임시 wav 사용 후 삭제).

    빈 텍스트는 no-op. 합성/재생 오류는 subprocess 예외 전파.
    """
    safe = _sanitize(text)
    if not safe:
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name
    try:
        synthesize(safe, model_path, wav_path, piper_bin=piper_bin, timeout=timeout)
        subprocess.run(
            [player, wav_path],
            check=True,
            timeout=timeout,
            capture_output=True,
        )
    finally:
        Path(wav_path).unlink(missing_ok=True)
