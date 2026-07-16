#!/usr/bin/env bash
# run_isolation_buffer.sh — ADR-0050 D1 격리 검증 격자 (제동 버퍼 ON) 실행/재개.
#
# 격자: 합성 신뢰도 {c_constant_1, c_constant_mid, c_stall} × {B0,B1a,B1b,B2}
#       × {S5,S6} × 10ep = 240 trial. 백본 무의미(합성 c) → 단일 gemma-4-e4b.
# 제동 버퍼: TIER1_BRAKE_BUFFER_M (ADR-0050 D2) 로 단일적분기 CBF↔PX4 속도추적 지연
#       상대차수 간극이 만드는 경계 overshoot(≈v·τ)를 흡수 → 물리 하한 r_min 유지.
#       스모크 검증: 버퍼 OFF B2@c=1 V_floor=0.64 → 버퍼 0.15 ON V_floor=0.00.
# LLM: **네이티브 ollama(11500)** — docker edge_ollama(VM 메모리 갇힘) 아님.
#
# ★ SSH 끊김 내성: `nohup bash scripts/run_isolation_buffer.sh > <log> 2>&1 &` 로 띄우면
#   SSH 종료 후에도 계속 돈다. run_grid --resume 내장(fetch_plan --resume)이라 죽거나
#   중단돼도 이 스크립트를 *다시 실행*하면 완료 trial 건너뛰고 이어진다(RUN_DIR 고정).
#
# 전제 (별 셸, 이미 기동돼 있어야 함):
#   - 영속 sim 셸: HEADLESS=1 SIGMA_BRIDGE=1 SIGMA_STANDOFF=0 SCENARIO=livingroom ./scripts/up.sh
#     (Track B 는 SIGMA_STANDOFF=0 필수 — 사용자 회피영역 침입이 r_min 경계에 걸리게)
#   - 네이티브 ollama: OLLAMA_HOST=0.0.0.0:11500 nohup ollama serve &  (gemma4:e4b 보유)
#
# 사용:
#   nohup bash scripts/run_isolation_buffer.sh > /tmp/isolation_buf.log 2>&1 &   # 최초/재개
#   BUILD= bash scripts/run_isolation_buffer.sh                                   # 빌드 생략 재개
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH=/usr/local/bin:/opt/homebrew/bin:$PATH

RUN_DIR="${RUN_DIR:-results/runs/adr0050_isolation_buf015}"   # 고정 — 재실행 시 resume
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11500}"
export TIER1_BRAKE_BUFFER_M="${TIER1_BRAKE_BUFFER_M:-0.15}"
BACKBONE="${BACKBONE:-gemma-4-e4b}"
EPISODES="${EPISODES:-10}"
BUILD="${BUILD:---build}"     # 최초 실행 빌드(idempotent). 빌드 생략 재개 시 BUILD= 로.

[ -x .venv/bin/python3 ] || { echo "ERROR: .venv/bin/python3 없음"; exit 1; }

# provenance manifest (run_grid 은 자체 manifest 없음 — 재현·추적용 최소 기록).
mkdir -p "$RUN_DIR"
{
  echo "run: adr0050_isolation_buffer"
  echo "started_or_resumed: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git_sha: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "git_branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  echo "git_dirty: $([ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ] && echo true || echo false)"
  echo "brake_buffer_m: $TIER1_BRAKE_BUFFER_M"
  echo "ollama_base_url: $OLLAMA_BASE_URL"
  echo "backbone: $BACKBONE   episodes: $EPISODES"
  echo "profiles: c_constant_1 c_constant_mid c_stall"
  echo "---"
} >> "$RUN_DIR/manifest_isolation.txt"

echo "[isolation] RUN_DIR=$RUN_DIR  buffer=$TIER1_BRAKE_BUFFER_M  ollama=$OLLAMA_BASE_URL"
echo "[isolation] 240 trial (4 baseline × 2 scenario × 3 profile × 10ep), 단일 백본 $BACKBONE"

.venv/bin/python3 scripts/run_grid.py --track-b \
  --confidence-profiles c_constant_1 c_constant_mid c_stall \
  --backbone "$BACKBONE" --n-episodes "$EPISODES" \
  --output-root "$RUN_DIR" $BUILD "$@"
rc=$?
echo "[isolation] run_grid 종료 rc=$rc ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo "[isolation] 재개(중단 시): BUILD= bash scripts/run_isolation_buffer.sh"
exit $rc
