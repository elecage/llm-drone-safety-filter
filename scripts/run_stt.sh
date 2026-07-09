#!/usr/bin/env bash
# run_stt.sh — whisper-server 기동 + STT 파이프라인 실행 (ADR-0015)
#
# 사용:
#   ./scripts/run_stt.sh              # 스페이스바 모드 (Accessibility 권한 필요)
#   ./scripts/run_stt.sh --stdin      # Enter 키 모드 (Accessibility 권한 불필요)
#   ./scripts/run_stt.sh --loop       # 마이크 + 명료화 루프 (대화 누적 + TTS)
#   CONTAINER_NAME=my-sim WHISPER_PORT=9000 ./scripts/run_stt.sh --stdin
#
# 환경 변수:
#   VOICE_LANG=ko|en|auto         (기본 auto) — STT·TTS 공통 언어 (음성 도메인 단일 키)
#                                   ko   → whisper ko 강제 + TTS Yuna (한국어 voice)
#                                   en   → whisper en 강제 + TTS Samantha (영어 voice)
#                                   auto → whisper 자동 감지 + TTS 매 발화 텍스트로 자동 선택
#
# 선행 조건:
#   ./scripts/install_whisper_cpp.sh 실행 완료
#   up.sh 로 Docker 컨테이너 가동 중
#
# 종료: Ctrl+C (whisper-server도 함께 종료됨)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# .env 자동 로드 — start_intent_stack.sh 와 동일 패턴. VOICE_LANG 등을 한 곳
# (.env)에서 설정하면 STT·TTS(본 스크립트)와 wrapper(start_intent_stack)가
# 같은 값으로 뜬다. 별도로 주던 두 스크립트의 VOICE_LANG 불일치(STT=ko 인데
# wrapper=auto → 명료화 질문이 엉뚱한 언어로 생성)를 방지.
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

MODEL_DIR="${HOME}/.cache/whisper"
MODEL="${WHISPER_MODEL:-ggml-large-v3-turbo.bin}"
PORT="${WHISPER_PORT:-8765}"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
# VOICE_LANG — STT·TTS 공통 언어 (음성 도메인 단일 변수). 'auto' 면 자동 감지.
# whisper-server 기본은 'en' (영어 강제) — 한국어 발화가 영어로 잘못 transcription 됨.
# 따라서 default = 'auto' 로 강제 (한국어/영어 혼용 가능). 정확도 ↑ 위해
# VOICE_LANG=ko (한국어 전용) 또는 VOICE_LANG=en (영어 전용) 권장.
VOICE_LANG="${VOICE_LANG:-auto}"
EXTRA_ARGS="${1:-}"  # --stdin 등 추가 인자를 파이프라인에 그대로 전달

log() { echo "[run_stt] $*"; }

# ------------------------------------------------------------------
# 검사
# ------------------------------------------------------------------
if ! command -v whisper-server >/dev/null 2>&1; then
    echo "ERROR: whisper-server 미발견 — ./scripts/install_whisper_cpp.sh 실행 후 재시도" >&2
    exit 1
fi
if [ ! -f "$MODEL_DIR/$MODEL" ]; then
    echo "ERROR: 모델 미발견: $MODEL_DIR/$MODEL" >&2
    echo "       ./scripts/install_whisper_cpp.sh 실행 후 재시도" >&2
    exit 1
fi
if [ ! -f "$REPO_ROOT/.venv/bin/python" ]; then
    echo "ERROR: .venv 미발견" >&2
    exit 1
fi

# ------------------------------------------------------------------
# whisper-server 시작 (백그라운드)
# ------------------------------------------------------------------
log "whisper-server 시작 (모델: $MODEL, 포트: $PORT, 언어: $VOICE_LANG) ..."
whisper-server \
    -m "$MODEL_DIR/$MODEL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --inference-path /inference \
    --language "$VOICE_LANG" \
    > /tmp/whisper_server.log 2>&1 &
WHISPER_PID=$!
log "    PID=$WHISPER_PID"

cleanup() {
    log "종료 — whisper-server (PID=$WHISPER_PID) 정지"
    kill "$WHISPER_PID" 2>/dev/null || true
    # --loop 의 처분 음성 구독 tts_pipeline 동반 종료.
    kill "${SPEECH_TTS_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 서버 준비 대기
log "서버 준비 대기 (최대 10s) ..."
for i in $(seq 1 10); do
    if curl -s "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        log "    ✓ 서버 응답 확인 (${i}s)"
        break
    fi
    sleep 1
done

# ------------------------------------------------------------------
# STT 파이프라인 실행
# ------------------------------------------------------------------
log "STT 파이프라인 시작 (컨테이너: $CONTAINER)"
case "$EXTRA_ARGS" in
    --loop)
        log "    명료화 루프 모드 — Enter 로 push-to-talk, 대화 누적 + TTS 응답."
        log "    선행: start_intent_stack.sh MODE=fusion 가동 필요."
        log "    언어: VOICE_LANG=$VOICE_LANG (STT·TTS 공통)"
        log ""
        # 실행 처분 음성 — sigma_bridge 가 실제 동작(이동/projection/hover)을
        # /intent/speech_out 에 발행 → 이 tts_pipeline 이 say. ask_user 질문은
        # clarification_loop 가 자체 say (별 경로, 중복 없음).
        TTS_TOPIC=/intent/speech_out \
        CONTAINER_NAME="$CONTAINER" \
        SAY_VOICE="auto" \
        "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/tts_pipeline.py" \
            > /tmp/tts_speech_out.log 2>&1 &
        SPEECH_TTS_PID=$!
        log "    처분 음성 구독 tts_pipeline 시작 (PID=$SPEECH_TTS_PID, topic=/intent/speech_out)"
        WHISPER_URL="http://127.0.0.1:${PORT}/inference" \
        CONTAINER_NAME="$CONTAINER" \
        STT_MODE=mic \
        VOICE_LANG="$VOICE_LANG" \
        "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/clarification_loop.py"
        ;;
    --stdin)
        log "    Enter 키로 녹음 시작/중지."
        log ""
        WHISPER_URL="http://127.0.0.1:${PORT}/inference" \
        CONTAINER_NAME="$CONTAINER" \
        "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/stt_pipeline.py" --stdin
        ;;
    *)
        log "    스페이스바를 누르는 동안 말하세요."
        log ""
        WHISPER_URL="http://127.0.0.1:${PORT}/inference" \
        CONTAINER_NAME="$CONTAINER" \
        "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/stt_pipeline.py" $EXTRA_ARGS
        ;;
esac
