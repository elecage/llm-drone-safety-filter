#!/usr/bin/env bash
# install_whisper_cpp.sh — whisper.cpp (Homebrew) + ggml 모델 다운로드 (ADR-0015 D2)
#
# 사용:
#   ./scripts/install_whisper_cpp.sh
#   MODEL=large-v3 ./scripts/install_whisper_cpp.sh   # 모델 변경
#
# 산출물:
#   /usr/local/bin/whisper-server  (또는 /opt/homebrew/bin/)
#   $HOME/.cache/whisper/ggml-<MODEL>.bin

set -euo pipefail

MODEL="${MODEL:-large-v3-turbo}"
MODEL_DIR="${HOME}/.cache/whisper"
MODEL_FILE="ggml-${MODEL}.bin"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_FILE}"

log() { echo "[install_whisper_cpp] $*"; }

# ------------------------------------------------------------------
# 1. whisper-cpp (Homebrew)
# ------------------------------------------------------------------
log "1/3 brew install whisper-cpp ..."
if command -v whisper-server >/dev/null 2>&1; then
    log "    이미 설치됨: $(whisper-server --version 2>&1 | head -1 || echo 'version unknown')"
else
    brew install whisper-cpp
fi

# ------------------------------------------------------------------
# 2. 모델 다운로드
# ------------------------------------------------------------------
log "2/3 모델 다운로드 확인 ($MODEL_FILE) ..."
mkdir -p "$MODEL_DIR"
if [ -f "$MODEL_DIR/$MODEL_FILE" ]; then
    log "    이미 존재: $MODEL_DIR/$MODEL_FILE ($(du -sh "$MODEL_DIR/$MODEL_FILE" | cut -f1))"
else
    log "    다운로드 시작 — $MODEL_URL"
    log "    (large-v3-turbo ≈ 1.6 GB, large-v3 ≈ 2.9 GB)"
    curl -L --progress-bar -o "$MODEL_DIR/$MODEL_FILE" "$MODEL_URL"
    log "    ✓ 저장: $MODEL_DIR/$MODEL_FILE"
fi

# ------------------------------------------------------------------
# 3. host venv Python 의존성
# ------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
log "3/3 host venv 의존성 설치 (sounddevice, pynput, requests, numpy) ..."
if [ ! -f "$VENV/bin/pip" ]; then
    echo "ERROR: .venv 미발견 — 먼저 venv를 생성하세요." >&2
    exit 1
fi
# pip 최신화 선행 — venv 생성 시점의 낡은 pip(예: 21.x)는 pynput 의 macOS
# 의존성 pyobjc-core 의 *미리 빌드된 wheel* 을 못 찾고 소스 빌드로 빠지는데,
# macOS 26 의 clang 이 -Wdefault-const-init-var-unsafe 를 error 로 막아
# 빌드 실패한다 (실측 2026-06-11). 최신 pip + --prefer-binary 로 wheel 사용.
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --prefer-binary sounddevice pynput requests numpy

log ""
log "✓ 설치 완료"
log ""
log "  서버 실행: whisper-server -m $MODEL_DIR/$MODEL_FILE --host 127.0.0.1 --port 8765"
log "  STT 실행:  ./scripts/run_stt.sh"
