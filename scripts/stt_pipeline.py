#!/usr/bin/env python3
"""STT 파이프라인 — Push-to-talk / Enter-key → whisper.cpp → /intent/user_prompt_raw.

사용:
    # 스페이스바 모드 (macOS Accessibility 권한 필요)
    WHISPER_URL=http://127.0.0.1:8765/inference \\
    CONTAINER_NAME=llmdrone-sim \\
    .venv/bin/python scripts/stt_pipeline.py

    # Enter 키 모드 (Accessibility 권한 불필요)
    .venv/bin/python scripts/stt_pipeline.py --stdin

    직접 실행은 scripts/run_stt.sh 를 통해 하는 것을 권장.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# host venv에서 intent_stt 패키지가 설치되어 있지 않은 경우를 위한 fallback path
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "intent" / "stt"))

from intent_stt.mic_capture import PushToTalkCapture
from intent_stt.ros_bridge import publish_utterance
from intent_stt.whisper_client import transcribe

WHISPER_URL = os.environ.get("WHISPER_URL", "http://127.0.0.1:8765/inference")
CONTAINER = os.environ.get("CONTAINER_NAME", "llmdrone-sim")
DEBUG = os.environ.get("STT_DEBUG", "0") == "1"


def _process_audio(audio, container: str) -> None:
    """녹음 버퍼를 whisper 변환 후 ROS 발행."""
    import numpy as np

    n_samples = len(audio)
    duration_s = n_samples / 16000

    if DEBUG:
        rms = float(np.sqrt(np.mean(audio**2))) if n_samples > 0 else 0.0
        print(f"[DBG] 오디오: {n_samples} 샘플 ({duration_s:.2f}s) RMS={rms:.4f}", flush=True)

    if n_samples < 1600:  # 0.1s 미만 → 전송하지 않음
        print(f"[STT] ─ 너무 짧음 ({duration_s:.2f}s) — 더 길게 말해보세요.")
        return

    print(f"[STT] ◌ 변환 중... ({duration_s:.1f}s)", end="\r", flush=True)

    try:
        text = transcribe(audio, url=WHISPER_URL)
    except ConnectionError:
        print(f"[STT] ✗ whisper-server 연결 실패 ({WHISPER_URL})")
        print(f"      → run_stt.sh 로 실행하거나 whisper-server 가 떠 있는지 확인")
        return
    except Exception as exc:
        print(f"[STT] ✗ whisper 오류: {exc}          ")
        return

    if DEBUG:
        print(f"[DBG] whisper 원문: {text!r}", flush=True)

    text = text.strip()
    if not text:
        print(f"[STT] ─ 인식 결과 없음 (whisper가 무음으로 판단)          ")
        return

    # whisper hallucination 필터: 구두점·공백만 남은 응답 무시
    if re.fullmatch(r"[.。!?…\s]+", text):
        print(f"[STT] ─ 무시됨 (hallucination: {text!r})          ")
        return

    print(f"[STT] ✓ 인식: {text!r}          ")

    try:
        publish_utterance(text, container=container)
        print(f"[STT]   → /intent/user_prompt_raw 발행 완료")
    except Exception as exc:
        print(f"[STT] ✗ ROS 발행 오류: {exc}")


def main_stdin() -> None:
    """Enter 키 방식 — Accessibility 권한 불필요."""
    cap = PushToTalkCapture()
    print("[STT] Enter 키 모드 — Enter로 녹음 시작, 다시 Enter로 중지·전송. Ctrl+C로 종료.")
    print(f"      whisper: {WHISPER_URL}  container: {CONTAINER}")
    print(f"      디버그: STT_DEBUG=1 로 오디오 상세 출력 가능")
    print()
    try:
        while True:
            try:
                input("  → [Enter] 녹음 시작 ")
            except EOFError:
                break
            cap.start()
            print("[STT] ● 녹음 중... (Enter 키로 중지)")
            try:
                input()
            except EOFError:
                cap.stop()
                break
            audio = cap.stop()
            _process_audio(audio, CONTAINER)
    except KeyboardInterrupt:
        pass
    finally:
        cap.close()
        print("\n[STT] 종료.")


def main() -> None:
    """스페이스바 Push-to-talk 방식 — macOS Accessibility 권한 필요."""
    from pynput import keyboard as kb

    def _is_space(key: object) -> bool:
        """Key.space 또는 KeyCode(char=' ') 두 가지 형태 모두 처리."""
        return key == kb.Key.space or (hasattr(key, "char") and key.char == " ")

    cap = PushToTalkCapture()
    print("[STT] 준비 완료 — 스페이스바를 누르는 동안 말하세요. Ctrl+C로 종료.")
    print(f"      whisper: {WHISPER_URL}  container: {CONTAINER}")
    print(f"      디버그: STT_DEBUG=1 로 키·오디오 상세 출력 가능")
    print()

    def on_press(key: object) -> None:
        if DEBUG:
            print(f"[DBG] on_press: {key!r}", flush=True)
        if _is_space(key):
            cap.start()
            print("[STT] ● 녹음 중...", end="\r", flush=True)

    def on_release(key: object) -> None:
        if DEBUG:
            print(f"[DBG] on_release: {key!r}", flush=True)
        if not _is_space(key):
            return
        audio = cap.stop()
        _process_audio(audio, CONTAINER)

    listener = kb.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        pass
    finally:
        cap.close()
        listener.stop()
        print("\n[STT] 종료.")


if __name__ == "__main__":
    if "--stdin" in sys.argv:
        main_stdin()
    else:
        main()
