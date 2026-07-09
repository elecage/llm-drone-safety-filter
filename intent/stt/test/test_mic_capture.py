"""intent_stt.mic_capture 단위 테스트 — sounddevice mock."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from intent_stt.mic_capture import PushToTalkCapture, SAMPLE_RATE


@pytest.fixture
def cap():
    mock_stream = MagicMock()
    with patch("intent_stt.mic_capture.sd.InputStream", return_value=mock_stream):
        c = PushToTalkCapture()
    yield c
    c.close()


def test_stop_before_start_returns_empty(cap):
    audio = cap.stop()
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert len(audio) == 0


def test_start_sets_active_and_clears_chunks(cap):
    cap._chunks = [np.ones(100, dtype="float32")]
    cap.start()
    assert cap._active is True
    assert cap._chunks == []


def test_stop_clears_active_flag(cap):
    cap.start()
    cap.stop()
    assert cap._active is False


def test_callback_records_when_active(cap):
    cap.start()
    indata = np.zeros((160, 1), dtype="float32")
    cap._cb(indata, 160, None, None)
    assert len(cap._chunks) == 1
    assert cap._chunks[0].shape == (160,)


def test_callback_ignored_when_inactive(cap):
    indata = np.zeros((160, 1), dtype="float32")
    cap._cb(indata, 160, None, None)
    assert len(cap._chunks) == 0


def test_stop_concatenates_chunks(cap):
    cap.start()
    cap._chunks = [np.ones(100, dtype="float32"), np.ones(200, dtype="float32")]
    audio = cap.stop()
    assert len(audio) == 300
    assert np.all(audio == 1.0)


def test_close_calls_stream_methods(cap):
    cap.close()
    cap._stream.stop.assert_called_once()
    cap._stream.close.assert_called_once()


def test_sample_rate_passed_to_stream():
    mock_stream = MagicMock()
    with patch("intent_stt.mic_capture.sd.InputStream", return_value=mock_stream) as mock_cls:
        cap = PushToTalkCapture(sample_rate=8000)
        cap.close()
    _, kwargs = mock_cls.call_args
    assert kwargs["samplerate"] == 8000


def test_default_sample_rate():
    mock_stream = MagicMock()
    with patch("intent_stt.mic_capture.sd.InputStream", return_value=mock_stream) as mock_cls:
        cap = PushToTalkCapture()
        cap.close()
    _, kwargs = mock_cls.call_args
    assert kwargs["samplerate"] == SAMPLE_RATE
