#!/usr/bin/env bash
# install_piper.sh — Piper TTS + voice model 설치 (ADR-0016 D2)
#
# 사용:
#   ./scripts/install_piper.sh                  # ko_KR 기본 voice
#   PIPER_VOICE=en_US-lessac-medium ./scripts/install_piper.sh
#
# 산출물:
#   piper 바이너리 (pip 또는 brew)
#   $HOME/.cache/piper/<voice>.onnx + .onnx.json
#
# STT(install_whisper_cpp.sh)의 TTS 대칭 — 명료화 루프 출력단.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE="${PIPER_VOICE:-ko_KR-glow-medium}"
VOICE_DIR="${HOME}/.cache/piper"
# Piper voice 저장소 (Rhasspy huggingface). 한국어 voice 가 없으면 README 안내.
HF_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"

log() { echo "[install_piper] $*"; }

# ------------------------------------------------------------------
# 1. piper 설치 (venv pip 우선, 실패 시 brew 안내)
# ------------------------------------------------------------------
log "1/2 piper 설치 확인 ..."
if command -v piper >/dev/null 2>&1; then
    log "    이미 설치됨: $(command -v piper)"
elif [ -f "$REPO_ROOT/.venv/bin/pip" ]; then
    log "    venv pip 로 piper-tts 설치 ..."
    "$REPO_ROOT/.venv/bin/pip" install piper-tts || {
        echo "    WARN: pip piper-tts 실패 — 'brew install piper-tts' 수동 시도 권장" >&2
    }
else
    echo "ERROR: .venv 미발견 + piper 미설치 — brew install piper-tts 또는 venv 생성 후 재시도" >&2
    exit 1
fi

# ------------------------------------------------------------------
# 2. voice model 다운로드
# ------------------------------------------------------------------
log "2/2 voice model 확인 ($VOICE) ..."
mkdir -p "$VOICE_DIR"
if [ -f "$VOICE_DIR/${VOICE}.onnx" ]; then
    log "    이미 존재: $VOICE_DIR/${VOICE}.onnx"
else
    log "    ⚠️  voice 자동 다운로드는 voice 경로가 Rhasspy 저장소 구조에 따라 다름."
    log "    수동 다운로드 안내 (예 ko_KR):"
    log "      python3 -m piper.download_voices ${VOICE}   # piper-tts 내장 다운로더"
    log "      또는 ${HF_BASE}/ko/ko_KR/... 에서 .onnx + .onnx.json 받아"
    log "      $VOICE_DIR/ 에 배치."
    log "    (한국어 voice 식별자는 piper-voices 저장소에서 확인)"
fi

cat <<DONE

[install_piper] 완료 확인 후 실행:
  PIPER_MODEL=$VOICE_DIR/${VOICE}.onnx ./scripts/run_tts.sh
DONE
