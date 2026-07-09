"""intent_stt.whisper_client 단위 테스트 — HTTP mock."""

from __future__ import annotations

import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from intent_stt.whisper_client import (
    SAMPLE_RATE,
    _MIN_DURATION_S,
    _to_wav,
    transcribe,
)


def _make_audio(duration_s: float = 1.0) -> np.ndarray:
    n = int(duration_s * SAMPLE_RATE)
    return np.zeros(n, dtype="float32")


def _mock_resp(text: str) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"text": text}
    r.raise_for_status.return_value = None
    return r


# --- _to_wav ---

def test_to_wav_is_valid_wav():
    audio = _make_audio(0.5)
    wav = _to_wav(audio)
    buf = io.BytesIO(wav)
    with wave.open(buf) as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == SAMPLE_RATE
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == len(audio)


def test_to_wav_clips_above_1():
    audio = np.array([2.0, -2.0], dtype="float32")
    wav = _to_wav(audio)
    buf = io.BytesIO(wav)
    with wave.open(buf) as wf:
        frames = wf.readframes(2)
    import struct
    vals = struct.unpack("<hh", frames)
    assert vals[0] == 32767
    assert vals[1] == -32767


# --- transcribe ---

def test_transcribe_returns_stripped_text():
    audio = _make_audio(1.0)
    with patch("intent_stt.whisper_client.requests.post", return_value=_mock_resp("  hello world  ")):
        result = transcribe(audio)
    assert result == "hello world"


def test_transcribe_empty_server_response():
    audio = _make_audio(1.0)
    with patch("intent_stt.whisper_client.requests.post", return_value=_mock_resp("")):
        result = transcribe(audio)
    assert result == ""


def test_transcribe_too_short_skips_request():
    short = _make_audio(_MIN_DURATION_S * 0.5)
    with patch("intent_stt.whisper_client.requests.post") as mock_post:
        result = transcribe(short)
    mock_post.assert_not_called()
    assert result == ""


def test_transcribe_sends_wav_file():
    audio = _make_audio(1.0)
    with patch("intent_stt.whisper_client.requests.post", return_value=_mock_resp("ok")) as mock_post:
        transcribe(audio)
    _, kwargs = mock_post.call_args
    assert "files" in kwargs
    fname, fdata, fmime = kwargs["files"]["file"]
    assert fname == "audio.wav"
    assert fmime == "audio/wav"


def test_transcribe_sends_language_when_specified():
    audio = _make_audio(1.0)
    with patch("intent_stt.whisper_client.requests.post", return_value=_mock_resp("ok")) as mock_post:
        transcribe(audio, language="en")
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["language"] == "en"


def test_transcribe_skips_language_for_auto():
    audio = _make_audio(1.0)
    with patch("intent_stt.whisper_client.requests.post", return_value=_mock_resp("ok")) as mock_post:
        transcribe(audio, language="auto")
    _, kwargs = mock_post.call_args
    assert "language" not in kwargs.get("data", {})


def test_transcribe_propagates_connection_error():
    audio = _make_audio(1.0)
    with patch("intent_stt.whisper_client.requests.post", side_effect=ConnectionError("refused")):
        with pytest.raises(ConnectionError):
            transcribe(audio)


def test_transcribe_propagates_http_error():
    import requests as req
    audio = _make_audio(1.0)
    mock_r = MagicMock()
    mock_r.raise_for_status.side_effect = req.HTTPError("500")
    with patch("intent_stt.whisper_client.requests.post", return_value=mock_r):
        with pytest.raises(req.HTTPError):
            transcribe(audio)
