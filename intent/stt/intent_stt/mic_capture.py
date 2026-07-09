"""Push-to-talk 마이크 캡처 — sounddevice 기반 (ADR-0015 D2)."""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
_CHANNELS = 1


class PushToTalkCapture:
    """스페이스바 누르는 동안 마이크 입력을 누적, 떼면 반환."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sr = sample_rate
        self._chunks: list[np.ndarray] = []
        self._active = False
        self._lock = threading.Lock()
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=_CHANNELS,
            dtype="float32",
            callback=self._cb,
        )
        self._stream.start()

    def _cb(
        self,
        indata: np.ndarray,
        frames: int,
        time: object,
        status: object,
    ) -> None:
        if self._active:
            with self._lock:
                self._chunks.append(indata.copy().flatten())

    def start(self) -> None:
        with self._lock:
            self._chunks = []
            self._active = True

    def stop(self) -> np.ndarray:
        with self._lock:
            self._active = False
            chunks = list(self._chunks)
        if not chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(chunks)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
