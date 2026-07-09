#!/usr/bin/env bash
# C33 sub-task (a) — Ollama backend smoke test.
#
# Mac mini M4 24GB 에서 실행. ADR-0014 D2 amendment (2026-05-27) 측 3 local 백본:
#   gemma4:e4b          (Gemma 4 E4B, FP16, ~4 GB)
#   qwen2.5-vl:7b       (Qwen2.5-VL 7B, Q8, ~8 GB)
#   llama3.2-vision:11b (Llama 3.2 11B-Vision, Q4, ~6 GB)
#
# Step:
#   1. ollama 설치 확인
#   2. Ollama daemon 기동 확인 (미기동 시 기동)
#   3. 3 모델 목록 확인 (미pull 시 안내)
#   4. 각 모델 smoke test — 간단한 prompt → JSON 응답 + skill 파싱
#   5. 메모리 예산 검증 (ADR-0014 D2 amendment 표)
#
# Usage:
#   ./scripts/c33_backend_smoke.sh [--skip-pull] [--model <ollama_tag>]
#
#   --skip-pull        : 모델 pull 건너뜀 (이미 pull 됐을 때)
#   --model <tag>      : 특정 모델만 테스트 (예: --model gemma4:e4b)
#
# Exit: 0 = 모두 PASS / 1 = FAIL 있음.
#
# 정합:
#   ADR-0014 D2 amendment (2026-05-27), docs/handover/HOST_SETUP.md

set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
SKIP_PULL=0
ONLY_MODEL=""
DAEMON_STARTED=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-pull)   SKIP_PULL=1; shift ;;
        --model)       ONLY_MODEL="$2"; shift 2 ;;
        *)             echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
done

# ADR-0014 D1/D2 amendment 측 Ollama 태그 + 예상 메모리(GB).
TAGS=("gemma4:e4b" "qwen2.5-vl:7b" "llama3.2-vision:11b")
declare -A MEM_GB=(
    ["gemma4:e4b"]=4
    ["qwen2.5-vl:7b"]=8
    ["llama3.2-vision:11b"]=6
)
SIM_MEM_GB=10
MEM_BUDGET_GB=18

PASS=0
FAIL=0

_pass() { echo "[PASS] $*"; ((PASS++)) || true; }
_fail() { echo "[FAIL] $*" >&2; ((FAIL++)) || true; }
_info() { echo "[c33]  $*"; }

# ──────────────────────────────────────────────────────── Step 1: 설치 확인

_info "1/5 ollama 설치 확인"
if ! command -v ollama &>/dev/null; then
    _fail "ollama 미설치 — brew install ollama 실행 후 재시도"
    echo ""
    echo "설치 명령:"
    echo "  brew install ollama"
    exit 1
fi
OLLAMA_VERSION=$(ollama --version 2>/dev/null || echo "(버전 확인 불가)")
_pass "ollama 설치 확인: ${OLLAMA_VERSION}"

# ──────────────────────────────────────────────────────── Step 2: daemon

_info "2/5 Ollama daemon 확인 (${OLLAMA_HOST})"
if ! curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    _info "daemon 미기동 — 백그라운드 기동 중..."
    ollama serve >/tmp/c33_ollama_serve.log 2>&1 &
    DAEMON_PID=$!
    DAEMON_STARTED=1
    # 최대 10초 대기.
    for _i in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        if curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
            break
        fi
    done
    if ! curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
        _fail "Ollama daemon 기동 실패 — /tmp/c33_ollama_serve.log 확인"
        exit 1
    fi
    _pass "daemon 기동 완료 (PID=${DAEMON_PID})"
else
    _pass "daemon 응답 확인"
fi

# ──────────────────────────────────────────────────────── Step 3: 모델 목록

_info "3/5 모델 목록 확인"
PULLED_MODELS=$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' || echo "")

for TAG in "${TAGS[@]}"; do
    if [[ -n "$ONLY_MODEL" && "$TAG" != "$ONLY_MODEL" ]]; then
        continue
    fi
    if echo "$PULLED_MODELS" | grep -qF "$TAG"; then
        _pass "${TAG} 이미 설치됨"
    else
        if [[ $SKIP_PULL -eq 1 ]]; then
            _fail "${TAG} 미설치 — --skip-pull 해제 후 pull 필요"
        else
            _info "  ${TAG} pull 시작 (수 GB, 시간 소요)..."
            if ollama pull "${TAG}" 2>/dev/null; then
                _pass "${TAG} pull 완료"
            else
                _fail "${TAG} pull 실패 — 네트워크 또는 모델명 확인"
            fi
        fi
    fi
done

# ──────────────────────────────────────────────────────── Step 4: smoke test

_info "4/5 모델 smoke test"

# _llm_prompt.py build_messages() 와 동일한 구조:
#   system: SYSTEM_PROMPT
#   user: "Scenario: S3\nCommand: \"...\""
# 명확한 RETURN_TO_DOCK 발화 — 정답: {"skill": "return_to_dock", "args": {}}
SYSTEM_PROMPT='You are an intent parsing assistant for an assistive drone system supporting a user with quadriplegia.\nParse the spoken command and return a JSON object for the intended drone action.\n\nAvailable skills:\n- move_to: Move the drone to a location. Args: {"position_description": "<target location>"}\n- inspect: Inspect an object or area. Args: {"target_id": "<object_id>", "viewpoint": "overview"|"close"|"top"}\n- return_to_dock: Return the drone to its charging dock. Args: {}\n- emergency_land: Land the drone immediately. Args: {}\n- ask_user: Ask the user to clarify. Args: {"question": "<clarifying question>"}\n\nOutput ONLY a JSON object with exactly this format:\n{"skill": "<skill_name>", "args": {<args_dict>}}\n\nIf the command is unclear or ambiguous, use ask_user.'

_smoke_payload() {
    local model_tag="$1"
    python3 -c "
import json, sys
payload = {
    'model': sys.argv[1],
    'messages': [
        {'role': 'system', 'content': sys.argv[2]},
        {'role': 'user', 'content': 'Scenario: S3\nCommand: \"충전 독으로 복귀해줘.\"'},
    ],
    'stream': False,
    'format': 'json',
    'think': False,
    'options': {'temperature': 0.0, 'num_predict': 64},
}
print(json.dumps(payload))
" "$model_tag" "$SYSTEM_PROMPT"
}

for TAG in "${TAGS[@]}"; do
    if [[ -n "$ONLY_MODEL" && "$TAG" != "$ONLY_MODEL" ]]; then
        continue
    fi
    _info "  smoke test: ${TAG} (최대 120s)"

    PAYLOAD=$(_smoke_payload "$TAG")
    RESPONSE=$(curl -sf -X POST "${OLLAMA_HOST}/api/chat" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" \
        --max-time 120 2>/dev/null || echo "")

    if [[ -z "$RESPONSE" ]]; then
        _fail "${TAG} — API 응답 없음 (timeout 또는 연결 오류)"
        continue
    fi

    PARSE_RESULT=$(python3 -c "
import json, sys
try:
    r = json.loads(sys.stdin.read())
    content = r.get('message', {}).get('content', '')
    parsed = json.loads(content)
    skill = parsed.get('skill', '(없음)')
    print('OK:' + skill)
except Exception as e:
    print('ERR:' + str(e)[:80])
" <<< "$RESPONSE" 2>/dev/null || echo "ERR:(파싱 실패)")

    if [[ "$PARSE_RESULT" == OK:* ]]; then
        SKILL="${PARSE_RESULT#OK:}"
        _pass "${TAG} smoke PASS — skill=${SKILL}"
    else
        ERR="${PARSE_RESULT#ERR:}"
        _fail "${TAG} smoke FAIL — ${ERR}"
    fi
done

# ──────────────────────────────────────────────────────── Step 5: 메모리 예산

_info "5/5 메모리 예산 검증 (ADR-0014 D2 amendment)"

# ollama ps — 현재 load된 모델 목록 + 메모리.
PS_JSON=$(curl -sf "${OLLAMA_HOST}/api/ps" 2>/dev/null || echo '{"models":[]}')
python3 - <<PYEOF
import json
data = json.loads('''${PS_JSON}''')
models = data.get('models', [])
if models:
    print("  현재 load된 모델:")
    for m in models:
        size_gb = m.get('size', 0) / 1e9
        vram_gb = m.get('size_vram', 0) / 1e9
        print(f"    {m['name']}: size={size_gb:.1f} GB vram={vram_gb:.1f} GB")
else:
    print("  (현재 load된 모델 없음 — smoke test 후 unload 완료)")
PYEOF

# ADR-0014 D2 amendment 표 기반 예산 검증.
echo "  예산 추정 (sim ~${SIM_MEM_GB} GB + 각 모델):"
for TAG in "${TAGS[@]}"; do
    if [[ -n "$ONLY_MODEL" && "$TAG" != "$ONLY_MODEL" ]]; then
        continue
    fi
    MEM="${MEM_GB[$TAG]}"
    TOTAL=$((SIM_MEM_GB + MEM))
    if [[ $TOTAL -le $MEM_BUDGET_GB ]]; then
        _pass "${TAG}: ${SIM_MEM_GB}+${MEM}=${TOTAL} GB ≤ ${MEM_BUDGET_GB} GB budget"
    else
        _fail "${TAG}: ${SIM_MEM_GB}+${MEM}=${TOTAL} GB > ${MEM_BUDGET_GB} GB budget 초과"
    fi
done

# ──────────────────────────────────────────────────────── 결과 요약

echo ""
echo "══════════════════════════════════════════════"
echo " C33 backend smoke: PASS=${PASS}  FAIL=${FAIL}"
echo "══════════════════════════════════════════════"

if [[ $DAEMON_STARTED -eq 1 ]]; then
    echo ""
    echo "[c33] smoke test 완료. ollama serve 프로세스가 백그라운드에서 실행 중."
    echo "      종료하려면: kill \$(pgrep -f 'ollama serve')"
fi

if [[ $FAIL -gt 0 ]]; then
    echo ""
    echo "다음 단계:"
    echo "  - FAIL 항목 위 안내 확인"
    echo "  - docs/handover/HOST_SETUP.md 참조"
    exit 1
fi

echo ""
echo "다음 단계 (sub-task b 정확도 측정):"
echo "  PYTHONPATH=intent/llm python3 scripts/c33_accuracy_bench.py \\"
echo "      --all-models --output-dir results/"
