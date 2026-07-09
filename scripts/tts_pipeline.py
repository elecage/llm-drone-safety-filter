#!/usr/bin/env python3
"""TTS 파이프라인 — ask_user 질문 토픽 구독 → Piper 음성 출력 (ADR-0016 D2/D3).

STT(stt_pipeline.py)의 역방향 — STT 는 음성→텍스트→발행, TTS 는 구독→텍스트→음성.
명료화 루프 출력단: sigma_bridge 가 /intent/ask_user_question 에 발행한 질문을
host 에서 Piper 로 음성 출력 → 사용자가 듣고 STT 로 응답 → 루프.

사용:
    # macOS say (한국어 기본 — 설치 불필요)
    CONTAINER_NAME=llmdrone-sim .venv/bin/python scripts/tts_pipeline.py

    # Piper (영어/cross-platform 옵션)
    TTS_BACKEND=piper PIPER_MODEL=~/.cache/piper/en_US-lessac-medium.onnx \\
    .venv/bin/python scripts/tts_pipeline.py

    직접 실행은 scripts/run_tts.sh 를 통하는 것을 권장.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "intent" / "tts"))

from intent_tts.tts_bridge import stream_questions

# say(macOS 한국어 기본) | piper(영어/cross-platform). ADR-0016 D2 amendment.
TTS_BACKEND = os.environ.get("TTS_BACKEND", "say")
CONTAINER = os.environ.get("CONTAINER_NAME", "llmdrone-sim")
# 구독 토픽 — 기본은 ask_user 질문. 실행 처분 음성(sigma_bridge 의 실제 동작
# 기반 피드백)을 들으려면 TTS_TOPIC=/intent/speech_out. run_stt --loop 가
# speech_out 구독 인스턴스를 동반 가동한다.
TTS_TOPIC = os.environ.get("TTS_TOPIC", "/intent/ask_user_question")
SAY_VOICE = os.environ.get("SAY_VOICE", "auto")  # auto = 텍스트 한글 여부로 ko/en 선택
PIPER_MODEL = os.environ.get("PIPER_MODEL", "")
PIPER_BIN = os.environ.get("PIPER_BIN", "piper")


def _make_speaker():
    """TTS_BACKEND 에 따라 (text)->None 음성 출력 함수 반환."""
    if TTS_BACKEND == "say":
        from intent_tts.say_client import speak as say_speak
        return lambda q: say_speak(q, voice=SAY_VOICE)
    if TTS_BACKEND == "piper":
        if not PIPER_MODEL or not Path(PIPER_MODEL).expanduser().exists():
            print(f"ERROR: TTS_BACKEND=piper 인데 PIPER_MODEL 미발견: {PIPER_MODEL!r}",
                  file=sys.stderr)
            return None
        from intent_tts.piper_client import speak as piper_speak
        model = str(Path(PIPER_MODEL).expanduser())
        return lambda q: piper_speak(q, model, piper_bin=PIPER_BIN)
    print(f"ERROR: 알 수 없는 TTS_BACKEND={TTS_BACKEND!r} — say|piper", file=sys.stderr)
    return None


def main() -> int:
    speaker = _make_speaker()
    if speaker is None:
        return 1
    print(f"[tts] 구독 시작 topic={TTS_TOPIC} (backend={TTS_BACKEND}, "
          f"container={CONTAINER}) — Ctrl+C 종료")

    def on_question(q: str) -> None:
        print(f"[tts] 🔊 {q}")
        try:
            speaker(q)
        except Exception as exc:  # noqa: BLE001 — 합성 실패해도 루프 유지
            print(f"[tts] 합성/재생 실패 (무시): {exc}", file=sys.stderr)

    try:
        stream_questions(on_question, container=CONTAINER, topic=TTS_TOPIC)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
