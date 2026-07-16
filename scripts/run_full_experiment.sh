#!/usr/bin/env bash
# run_full_experiment.sh — 실험을 돌리는 *유일한* 진입점 (실행 + 분석 일원화).
#
# ★★ 이 스크립트 하나로만 실험을 돌린다. 새 실행 스크립트를 만들거나, run_grid 를
#    손으로 체이닝하거나, ad-hoc bash 로 격자를 돌리지 말 것. 변형(에피소드·모델·
#    범위)은 전부 *파라미터*로 — 그래야 적은-ep 검증이 본런과 *동일 코드 경로*가 되어
#    검증 의미가 산다. (배경: 실행 스크립트 난립 + 결과 데이터 섞임으로 반복된 헛수고 —
#    [[one-experiment-script-and-run-dirs]]. ADR-0041.)
#
# 무엇을 보장하나:
#   1. 단일 진입점·양 다리 — 통합 스택 격자 + 하한 검증(Track B)을 한 번에(반쪽 실행 불가).
#   2. 격리된 run 디렉터리 — results/runs/<날짜시각>__<tag>/ 하나에 *그 run 의 모든 것*.
#      옛 데이터와 섞일 수 없음(provenance·무결성).
#   3. manifest.yaml 자동 기록 — git SHA·dirty·브랜치·파라미터·시각 → 재현·추적.
#   4. 실행+분석 일원화 — 끝나면 집계(metrics_aggregator)를 *같은 run 디렉터리*에 자동 산출.
#
# 사용 (모든 변형 = 파라미터):
#   ./scripts/run_full_experiment.sh                              # 본 풀런(3백본 × 10ep)
#   EPISODES=2 BACKBONES="gemma-4-e4b llama-3.2-11b-vision" \
#     RUN_TAG=smoke ./scripts/run_full_experiment.sh             # 적은-ep 검증(동일 경로)
#   DRY_RUN=1 ./scripts/run_full_experiment.sh                    # 계획만
#   SCENARIOS="S5 S6" LEGS=full_stack BASELINES=b4 FAULTS=adversarial_geofence \
#     RUN_TAG=adr0049_b4adv ./scripts/run_full_experiment.sh      # 표적 재수집(ADR-0049 D5)
#   BACKBONES=gemma-4-e4b CONFIDENCE_PROFILES="c_constant_1 c_constant_mid c_stall" \
#     RUN_TAG=adr0050_isolation ./scripts/run_full_experiment.sh  # 합성 신뢰도 격리(ADR-0050 D7)
#     # ↳ 하한 검증(Track B) 다리를 프로파일별로 확장. 신뢰도가 합성이라 백본 축 무의미
#     #   → 단일 백본으로 실행(ADR-0050 D1 "단일 구성"). live 통합 스택 다리는 불변.
#
# 분석은 *항상 특정 run 디렉터리*를 가리킨다 (results/ 통째 읽기 금지):
#   결과·집계 = results/runs/<RUN_ID>/  (실행 끝에 경로 출력).
#
# 전제 (1회, 별 터미널) — 영속 셸:
#   HEADLESS=1 DRONE_CAMERA=1 OVD=1 SIGMA_BRIDGE=1 SCENARIO=livingroom ./scripts/up.sh
set -uo pipefail
cd "$(dirname "$0")/.."

BACKBONES="${BACKBONES:-gemma-4-e4b llama-3.2-11b-vision gpt-4o}"
EPISODES="${EPISODES:-10}"
SCENARIOS="${SCENARIOS:-S5 S6}"      # 부분 재수집(예: ADR-0039 D6 셀 무효화) 시 "S5"
BASELINES="${BASELINES:-}"           # 부분 재수집 시 예: "b4" (빈값 = 컨테이너 전체 6종)
FAULTS="${FAULTS:-}"                 # 부분 재수집 시 예: "adversarial_geofence" (빈값 = 전체 5종)
LEGS="${LEGS:-both}"                 # both | full_stack (track_b 유효 시 재수집 생략)
CONFIDENCE_PROFILES="${CONFIDENCE_PROFILES:-}"  # 빈값=live(현행). 지정 시 하한 검증(Track B)
                                     # 다리를 합성 신뢰도 프로파일로 확장(ADR-0050 D7 격리).
RUN_TAG="${RUN_TAG:-}"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
DRY_RUN="${DRY_RUN:-0}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11435}"
export TRIAL_LOG_DIR="${TRIAL_LOG_DIR:-/tmp/llmdrone_trial_log}"

err() { echo "[full-exp] ERROR: $*" >&2; exit 1; }

[ -x .venv/bin/python3 ] || err ".venv/bin/python3 없음 — anaconda python 금지([[ops-anaconda-python-poisons-gz-dlopen]])."

# run 디렉터리 (격리·provenance). 날짜시각 + tag. tag 미지정 시 ep+백본 약식.
_n_bb=$(echo $BACKBONES | wc -w | tr -d ' ')
[ -z "$RUN_TAG" ] && RUN_TAG="${EPISODES}ep_${_n_bb}bb"
RUN_ID="$(date +%Y%m%dT%H%M)__${RUN_TAG}"
RUN_DIR="results/runs/${RUN_ID}"

# 백본 → ollama 모델 (edge 만).
ollama_model() {
  case "$1" in
    gemma-4-e4b) echo 'gemma4:e4b' ;;
    llama-3.2-11b-vision) echo 'llama3.2-vision:11b' ;;
    *) echo '' ;;
  esac
}
# 백엔드 프리플라이트 — 실제 추론 가능 fail-fast (edge=ollama chat 200, cloud=API 키).
preflight() {
  local bb="$1" model code
  model="$(ollama_model "$bb")"
  if [ -n "$model" ]; then
    code="$(docker exec "$CONTAINER" bash -lc \
      "curl -s -o /dev/null -w '%{http_code}' -X POST '${OLLAMA_BASE_URL%/}/api/chat' \
       -d '{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"stream\":false}'" \
      2>/dev/null)"
    [ "$code" = "200" ] || err "ollama '$model' chat $code (≠200) — host 네이티브 :11435 기동·모델 확인 ([[p5-backbone-serving-setup]])."
    echo "[full-exp] preflight OK: $bb ($model @ $OLLAMA_BASE_URL)"
  elif [ "$bb" = 'gpt-4o' ]; then
    [ -n "${OPENAI_API_KEY:-}" ] || err "gpt-4o 인데 OPENAI_API_KEY 없음 (.env)."
    echo "[full-exp] preflight OK: gpt-4o (cloud)"
  else
    echo "[full-exp] WARN: 알 수 없는 백본 '$bb' — 프리플라이트 생략."
  fi
}

_n_sc=$(echo $SCENARIOS | wc -w | tr -d ' ')
_n_b=6; [ -n "$BASELINES" ] && _n_b=$(echo $BASELINES | wc -w | tr -d ' ')
_n_f=5; [ -n "$FAULTS" ] && _n_f=$(echo $FAULTS | wc -w | tr -d ' ')
_n_cp=1; [ -n "$CONFIDENCE_PROFILES" ] && _n_cp=$(echo $CONFIDENCE_PROFILES | wc -w | tr -d ' ')
std_per=$((_n_sc * _n_b * _n_f * EPISODES))  # 시나리오 × baseline × fault
trk_per=$((_n_sc * 4 * 1 * EPISODES * _n_cp))  # S5,S6 × 4 baseline × 1 fault × 신뢰도 프로파일(격리)
[ "$LEGS" != "both" ] && trk_per=0       # 표시 버그 수정 — 다리2 생략 시 합산 제외
echo "=================================================================="
echo " ADR-0039 Full Experiment — 단일 파이프라인 (양 다리 + 자동 집계)"
echo "   run 디렉터리: $RUN_DIR"
echo "   백본: $BACKBONES ($_n_bb)   episodes: $EPISODES"
echo "   다리1 통합 스택: ${std_per}/백본 → 합 $((std_per * _n_bb))  (C1·C2-a·C3·RQ3)"
echo "   다리2 하한 검증: ${trk_per}/백본 → 합 $((trk_per * _n_bb))  (C2-b·RQ1)"
echo "   총 trial: $(((std_per + trk_per) * _n_bb))"
echo "   OLLAMA_BASE_URL=$OLLAMA_BASE_URL  TRIAL_LOG_DIR=$TRIAL_LOG_DIR"
echo "=================================================================="

for bb in $BACKBONES; do preflight "$bb"; done
[ "$DRY_RUN" = "1" ] && { echo "[full-exp] DRY_RUN — 계획만 출력, 실행 안 함."; exit 0; }

mkdir -p "$RUN_DIR"
# manifest — provenance·무결성. git dirty 면 *경고*(미커밋 코드로 실행 = stale install 위험류).
_git_sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
_git_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
# dirty = *추적 코드* 변경만(미추적 stray 파일 무시 — 재현성과 무관).
_git_dirty="$([ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ] && echo true || echo false)"
cat > "$RUN_DIR/manifest.yaml" <<EOF
run_id: ${RUN_ID}
started: $(date -u +%Y-%m-%dT%H:%M:%SZ)
git:
  sha: ${_git_sha}
  branch: ${_git_branch}
  dirty: ${_git_dirty}      # true = 미커밋 변경으로 실행 (재현성 주의)
params:
  backbones: [$(echo $BACKBONES | sed 's/ /, /g')]
  episodes: ${EPISODES}
  scenarios: [$(echo $SCENARIOS | sed 's/ /, /g')]
  baselines: [$([ -n "$BASELINES" ] && echo $BASELINES | sed 's/ /, /g' || echo all)]
  faults: [$([ -n "$FAULTS" ] && echo $FAULTS | sed 's/ /, /g' || echo all)]
  legs: [$([ "$LEGS" = both ] && echo "full_stack, lower_bound_track_b" || echo "full_stack")]
  confidence_profiles: [$([ -n "$CONFIDENCE_PROFILES" ] && echo $CONFIDENCE_PROFILES | sed 's/ /, /g' || echo live)]
  ollama_base_url: ${OLLAMA_BASE_URL}
EOF
[ "$_git_dirty" = "true" ] && echo "[full-exp] ⚠️ git dirty — 미커밋 코드로 실행(manifest 기록). 재현성 주의."
echo "[full-exp] manifest: $RUN_DIR/manifest.yaml (git ${_git_sha:0:8} dirty=$_git_dirty)"

first_build='--build'   # 첫 run_grid 만 grid 패키지 HEAD 빌드.
fail=0
for bb in $BACKBONES; do
  echo "===== [$(date +%H:%M)] $bb — 다리1 통합 스택 격자 (S5 S6, ${EPISODES}ep) → $RUN_DIR ====="
  .venv/bin/python3 scripts/run_grid.py $first_build --scenarios $SCENARIOS \
    ${BASELINES:+--baselines $BASELINES} ${FAULTS:+--faults $FAULTS} \
    --backbone "$bb" --n-episodes "$EPISODES" --output-root "$RUN_DIR" \
    || { echo "[full-exp] WARN: $bb 통합 스택 비정상(incomplete 가능) — 계속"; fail=1; }
  first_build=''
  if [ "$LEGS" = "both" ]; then
    echo "===== [$(date +%H:%M)] $bb — 다리2 하한 검증 격자 (Track B, ${EPISODES}ep) → $RUN_DIR ====="
    .venv/bin/python3 scripts/run_grid.py --track-b \
      ${CONFIDENCE_PROFILES:+--confidence-profiles $CONFIDENCE_PROFILES} \
      --backbone "$bb" --n-episodes "$EPISODES" --output-root "$RUN_DIR" \
      || { echo "[full-exp] WARN: $bb Track B 비정상 — 계속"; fail=1; }
  else
    echo "===== $bb — 다리2 생략 (LEGS=$LEGS — track_b 유효 재사용, ADR-0039 D6)"
  fi
done

# 실행+분석 일원화 — 집계를 *같은 run 디렉터리*에 자동 산출 (컨테이너: rosbag/px4_msgs 필요).
echo "===== [$(date +%H:%M)] 자동 집계 → $RUN_DIR/aggregate_<bb>.{json,md} ====="
for bb in $BACKBONES; do
  docker exec "$CONTAINER" bash -lc "
    source /opt/ros/humble/setup.bash; source /workspace/install/setup.bash 2>/dev/null
    cd /workspace
    PYTHONPATH=/workspace/eval/metrics:\$PYTHONPATH python3 -m eval_runner.metrics_aggregator \
      --output-root ${RUN_DIR} --backbone ${bb} \
      --md-out ${RUN_DIR}/aggregate_${bb}.md --json-out ${RUN_DIR}/aggregate_${bb}.json" \
    2>&1 | grep -E '작성|총.*trial|ERROR' || echo "[full-exp] WARN: $bb 집계 실패(데이터 확인)"
done

# manifest 종료 시각 + bag 수 기록.
{
  echo "finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "completed_trials: $(find "$RUN_DIR" -name metadata.yaml 2>/dev/null | wc -l | tr -d ' ')"
  echo "fail_flag: ${fail}      # 1 = 일부 leg incomplete (run_grid --resume 대상)"
} >> "$RUN_DIR/manifest.yaml"

echo "===== [$(date +%H:%M)] 풀런 완료 — run 디렉터리: $RUN_DIR (fail=$fail) ====="
echo "[full-exp] 결과·집계·manifest 전부 → $RUN_DIR (분석은 이 디렉터리만 읽을 것)"
echo "[full-exp] incomplete 있으면: run_grid.py --resume … --output-root $RUN_DIR"
exit $fail
