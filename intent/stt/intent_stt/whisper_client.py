"""whisper.cpp HTTP 서버 클라이언트 (ADR-0015 D2 — whisper-server Metal)."""

from __future__ import annotations

import io
import wave

import numpy as np
import requests

DEFAULT_URL = "http://127.0.0.1:8765/inference"
SAMPLE_RATE = 16000
_MIN_DURATION_S = 0.1


def _to_wav(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """float32 1-ch 배열 → WAV bytes (int16)."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def transcribe(
    audio: np.ndarray,
    url: str = DEFAULT_URL,
    timeout: float = 30.0,
    language: str = "auto",
) -> str:
    """float32 오디오를 whisper-server에 POST하고 인식 텍스트를 반환.

    길이가 _MIN_DURATION_S 미만이면 빈 문자열 반환.
    서버 오류 시 requests.HTTPError / ConnectionError 를 그대로 전파.
    """
    if len(audio) < int(_MIN_DURATION_S * SAMPLE_RATE):
        return ""
    wav = _to_wav(audio)
    data: dict[str, str] = {"response_format": "json"}
    if language != "auto":
        data["language"] = language
    resp = requests.post(
        url,
        files={"file": ("audio.wav", wav, "audio/wav")},
        data=data,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()
