#!/usr/bin/env bash
# run_tts.sh — ask_user 질문 구독 → 음성 출력 (ADR-0016 D2/D3)
#
# 사용:
#   ./scripts/run_tts.sh                          # macOS say (auto: 텍스트 한글이면 Yuna, 영문이면 Samantha)
#   SAY_VOICE=Sora ./scripts/run_tts.sh           # voice 고정 (자동 선택 비활성)
#   SAY_VOICE_KO=Sora SAY_VOICE_EN=Alex ./scripts/run_tts.sh   # auto 모드 ko/en 별 voice 변경
#   TTS_BACKEND=piper PIPER_MODEL=... ./scripts/run_tts.sh   # Piper (영어/cross-platform)
#
# 선행 조건:
#   up.sh + start_intent_stack.sh 로 sigma_bridge 가동 중 (ask_user 발행)
#   (Piper backend 만 ./scripts/install_piper.sh 필요. say 는 macOS 내장)
#
# STT(run_stt.sh)의 TTS 대칭 — 명료화 루프 출력단. 종료: Ctrl+C.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="${TTS_BACKEND:-say}"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"

log() { echo "[run_tts] $*"; }

if [ ! -f "$REPO_ROOT/.venv/bin/python" ]; then
    echo "ERROR: .venv 미발견" >&2
    exit 1
fi

if [ "$BACKEND" = "say" ]; then
    if ! command -v say >/dev/null 2>&1; then
        echo "ERROR: macOS 'say' 미발견 — say backend 는 macOS 전용 (TTS_BACKEND=piper 대안)" >&2
        exit 1
    fi
    log "ask_user 질문 구독 → macOS say 음성 출력 (voice: ${SAY_VOICE:-auto} — auto 시 한글=${SAY_VOICE_KO:-Yuna} / 영문=${SAY_VOICE_EN:-Samantha})"
elif [ "$BACKEND" = "piper" ]; then
    PIPER_MODEL="${PIPER_MODEL:-${HOME}/.cache/piper/en_US-lessac-medium.onnx}"
    if ! command -v piper >/dev/null 2>&1; then
        echo "ERROR: piper 미발견 — ./scripts/install_piper.sh 실행 후 재시도" >&2
        exit 1
    fi
    if [ ! -f "$PIPER_MODEL" ]; then
        echo "ERROR: voice model 미발견: $PIPER_MODEL" >&2
        exit 1
    fi
    export PIPER_MODEL
    log "ask_user 질문 구독 → Piper 음성 출력 (모델: $(basename "$PIPER_MODEL"))"
else
    echo "ERROR: 알 수 없는 TTS_BACKEND=$BACKEND — say|piper" >&2
    exit 1
fi
log "    sigma_bridge 의 ask_user 발행을 기다립니다. Ctrl+C 종료."
log ""

TTS_BACKEND="$BACKEND" \
CONTAINER_NAME="$CONTAINER" \
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/tts_pipeline.py"
