#!/usr/bin/env bash
# start_intent_stack.sh — wrapper_node + sigma_bridge_node 빌드·시작 (ADR-0015 하류)
#
# 사용:
#   ./scripts/start_intent_stack.sh
#   BACKBONE=gpt-4o OPENAI_API_KEY=sk-... ./scripts/start_intent_stack.sh
#   BACKBONE=closed-vocabulary-keyword SCENARIO=S5 CONTAINER_NAME=llmdrone-sim \
#     ./scripts/start_intent_stack.sh
#
# 환경 변수:
#   BACKBONE         backbone 식별자 (기본: closed-vocabulary-keyword — API 키 불필요)
#   SCENARIO         scenario_id (기본: S5)
#   MODE             direct | fusion (기본: direct)
#                    direct = utterance 만 LLM 입력 (keyword 백본 등 좌표 불필요)
#                    fusion = context_graph_publisher 기동 + wrapper 가 장면 context
#                             (객체+좌표) 주입 → LLM 의도 좌표 그라운딩 (ADR-0027, C37b)
#   CONTAINER_NAME   Docker 컨테이너 이름 (기본: llmdrone-sim)
#   OPENAI_API_KEY   gpt-4o 등 cloud LLM 사용 시 필요
#   OLLAMA_BASE_URL  edge LLM(gemma-4-e4b 등) Ollama 주소
#                    (기본: http://host.docker.internal:11434 — 호스트 Ollama)
#   USER_GUARD_RADIUS_M
#                    sigma_bridge 사용자 회피 영역 데모 가드 반경 [m]
#                    (기본: 1.0 — r_min=0.9 + 마진 0.1. ADR-0028 D5 amendment).
#                    1.5 로 올리면 paper §C tier1 r_max 와 정합 (인접 가구 접근
#                    차단 시연용). 0 으로 두면 가드 비활성 (Track B fault
#                    injection 트랙용).
#   TARGET_STANDOFF_M
#                    sigma_bridge 객체 standoff 거리 [m] (기본: 0.7).
#   DETOUR_ARRIVAL_THRESHOLD_M
#                    우회 waypoint 도달 임계 [m] (기본: 0.5). PX4 추종 잔여
#                    ≈ 0.45 m 보다 크게. 너무 크면 우회 효과 약화.
#   TAKEOFF_ALT_M
#                    sigma_bridge z floor [m] (기본: 1.5). setpoint z 가
#                    이 값 미만이면 강제 takeoff_alt 로 올림 (가구·바닥
#                    충돌 회피, ADR-0028 D5). 0 으로 두면 z floor 비활성
#                    (ADR-0025 D9 Track B — cognitive_lapse z variant
#                    의 floor 가드 우회).
#   VOICE_LANG       STT·TTS 와 동일 — wrapper 시스템 프롬프트에 ask_user 질문
#                    언어 지시 주입 (ko/en/auto, 기본 auto = 사용자 명령 언어 따라감).
#                    run_stt.sh 와 같은 값으로 export 권장.
#   OVD=1            OVD detector_node 가동 (P1 strict e2e 입력 사슬, ADR-0024
#                    D1 b Docker CPU). /camera/image_raw (up.sh DRONE_CAMERA=1
#                    중계 필요) → /intent/ovd/detections. 기본 0.
#   OVD_VOCAB        OVD 정적 vocabulary (launch 형식 리스트 문자열).
#                    기본 "['couch','table','chair']" (ADR-0021 정적 vocabulary).
#   OVD_THROTTLE_HZ  OVD 추론 상한 주기 (기본 5.0 — ADR-0024 Docker CPU 정합).
#
# 예: 로컬 Ollama gemma (API 키 불필요):
#   BACKBONE=gemma-4-e4b ./scripts/start_intent_stack.sh
#
# 예: gemma + 좌표 그라운딩 (sim 비행, C37b 레벨 2):
#   BACKBONE=gemma-4-e4b MODE=fusion SCENARIO=S5 ./scripts/start_intent_stack.sh
#
# 종료: Ctrl+C (모든 노드 정지됨)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# .env 자동 로드 — OPENAI_API_KEY 등 키를 자식 프로세스(docker exec)에 전달.
# dotenv 표준 형식(export 키워드 없는 KEY=VALUE)도 set -a 로 자동 export.
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
BACKBONE="${BACKBONE:-closed-vocabulary-keyword}"
SCENARIO="${SCENARIO:-S5}"
MODE="${MODE:-direct}"
# Ollama edge LLM backbone(gemma-4-e4b 등) — 컨테이너에서 호스트 Ollama 접근.
# Docker Desktop(macOS)은 host.docker.internal 로 호스트에 닿음.
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
# sigma_bridge 운용 가드 파라미터 — sigma_bridge_node.py default 와 동기.
# ADR-0028 D5/D8 데모 운용 가드 layer (paper §C tier1 r_max=1.5 와 별 책임).
USER_GUARD_RADIUS_M="${USER_GUARD_RADIUS_M:-1.0}"
TARGET_STANDOFF_M="${TARGET_STANDOFF_M:-0.7}"
DETOUR_ARRIVAL_THRESHOLD_M="${DETOUR_ARRIVAL_THRESHOLD_M:-0.5}"
# z floor (ADR-0028 D5) — sigma_bridge_node.py default 와 동기. 0 으로 두면
# 비활성 (ADR-0025 D9 Track B fault injection 트랙용).
TAKEOFF_ALT_M="${TAKEOFF_ALT_M:-1.5}"
# VOICE_LANG — STT·TTS 와 공통. wrapper 컨테이너로 forward → _llm_prompt 가
# 시스템 프롬프트에 ask_user.question 언어 지시 주입 (ko/en/auto).
VOICE_LANG="${VOICE_LANG:-auto}"
# OVD (P1 strict e2e 입력 사슬) — 기본 미가동.
OVD="${OVD:-0}"
# OVD 정적 어휘 단일 진실 소스 = scenario_params.scene (scene ``ovd_class`` 파생).
# 종전 하드코딩 ['couch','table','chair'] 는 거실 referent 'sofa'·마당 'person' 을
# 빠뜨려 S5/S6/S8 grounding 영구 실패(검출 0→s1≈0→c=0, 세션 53 적발) → scene 에서
# 파생해 drift 차단. 전 장소 합집합 사용(단일 시나리오라도 referent ∈ 합집합 성립).
if [ -z "${OVD_VOCAB:-}" ]; then
    OVD_VOCAB="$(docker exec "$CONTAINER" bash -c \
      'cd /workspace && source install/setup.bash >/dev/null 2>&1 && python3 -c "from scenario_params.scene import ovd_vocabulary_launch_str; print(ovd_vocabulary_launch_str())"' 2>/dev/null | tail -n1)"
    OVD_VOCAB="${OVD_VOCAB:-['chair','cup','person','sofa','table']}"
fi
OVD_THROTTLE_HZ="${OVD_THROTTLE_HZ:-5.0}"
# Estimator live 모드 (P2 strict e2e — ADR-0020 Amendment 2026-05-31). 기본 미가동.
#   ESTIMATOR_MODE=live  → estimator_node 가 /intent/ovd/detections (s1) +
#                          /intent/llm_sigma_raw (s2/s3) 위 실 c̃ 산출 →
#                          /intent/grounding_confidence (tier1 b2 구독).
#                          OVD=1 (s1 source) 동반 권장 — 미동반 시 s1 부재→c=0.
#   ESTIMATOR_DOT_C_MAX  → 변화율 한도 [1/s] (기본 §7.1 시안 0.833).
#   ESTIMATOR_SIGMA_LATCH_S → referent latch TTL [s] (ADR-0020 amend 2026-06-11,
#                          발견 A). LLM sigma 는 발화당 1회 이벤트라 OVD 와 분리해
#                          latch. 기본 0 = 무한(새 sigma 대체까지). 양수면 TTL 후 c=0.
ESTIMATOR_MODE="${ESTIMATOR_MODE:-}"
ESTIMATOR_DOT_C_MAX="${ESTIMATOR_DOT_C_MAX:-0.833}"
ESTIMATOR_SIGMA_LATCH_S="${ESTIMATOR_SIGMA_LATCH_S:-0.0}"
LOG_DIR="/tmp/llmdrone_intent"

log()  { echo "[intent_stack] $*"; }
die()  { echo "[intent_stack] ERROR: $*" >&2; exit 1; }

# ------------------------------------------------------------------
# 선행 조건 검사
# ------------------------------------------------------------------
docker info >/dev/null 2>&1 || die "Docker 미실행"
docker inspect "$CONTAINER" >/dev/null 2>&1 || die "컨테이너 '$CONTAINER' 없음 — up.sh 실행 후 재시도"

# ------------------------------------------------------------------
# 기존 intent stack 노드 정리 — 재호출 시 두 인스턴스 공존 회피.
# 두 sigma_bridge 가 같은 토픽 구독하면 토픽 sub count baseline 가 변해
# clarification_loop listener race 발생 등 부작용. 항상 깨끗히 시작.
#
# pkill -f 가 일부 환경에서 silently 작동 안 하는 경우 발견 — PID 직접 kill 패턴
# 으로 견고화.
# ------------------------------------------------------------------
log "기존 intent stack 노드 정리 (있으면) ..."
docker exec "$CONTAINER" bash -c "
    ps aux | grep -E 'wrapper_node|sigma_bridge|context_graph|detector_node' | grep -v grep \
        | awk '{print \$2}' | xargs -r kill -9 2>/dev/null
" 2>/dev/null || true
sleep 2
# kill -9 로 죽은 노드가 FastDDS 공유메모리(SHM) 세그먼트를 좀비로 남기면
# 같은 토픽의 신규 reader 가 메시지를 영영 못 받는다 (대용량 /camera/image_raw
# 에서 실측 재현, 2026-06-11). fastdds shm clean 은 *좀비만* 제거 — 가동 중
# 노드(tier1·g1 등)의 세그먼트는 보존.
docker exec "$CONTAINER" bash -c "source /opt/ros_gz_ws/install/setup.bash; fastdds shm clean >/dev/null 2>&1" 2>/dev/null || true

# ------------------------------------------------------------------
# Cloud LLM 백본(gpt-*) 사용 시 컨테이너에 openai 설치 (Dockerfile 누락 보완).
# 컨테이너 재생성 시 휘발되므로 매 호출 시 idempotent 확인.
# ------------------------------------------------------------------
case "$BACKBONE" in
    gpt-*)
        # 키 없는 gpt-* 백본은 wrapper 가 기동은 되나 발화 시 인증 실패로
        # *조용히* 무응답이 된다 (60s 무응답으로 관측됨, 2026-06-11). 선제 차단.
        if [ -z "${OPENAI_API_KEY:-}" ]; then
            die "BACKBONE=$BACKBONE 인데 OPENAI_API_KEY 없음 — .env 에 'OPENAI_API_KEY=sk-...' 추가하거나, 키 없이 쓰려면 로컬 백본 사용: BACKBONE=gemma-4-e4b $0"
        fi
        if ! docker exec "$CONTAINER" python3 -c "import openai" 2>/dev/null; then
            log "openai 패키지 미설치 — 컨테이너에 설치 중 (cloud LLM 백본 필수) ..."
            docker exec "$CONTAINER" pip install --quiet openai 2>&1 | tail -3
        fi
        ;;
esac

# ------------------------------------------------------------------
# 빌드 (컨테이너 내 colcon — symlink-install)
# fusion mode 는 intent_context(context_graph_publisher) + scenario_params 추가.
# ------------------------------------------------------------------
# intent_sigma_bridge·scenario_params 는 sigma_bridge_node 가 항상 기동되므로 기본 포함
# (sigma_bridge 가 target_id → world 좌표 lookup 에 scenario_params 사용).
BUILD_PKGS="intent_llm intent_sigma_bridge scenario_params"
if [ "$MODE" = "fusion" ]; then
    BUILD_PKGS="intent_llm intent_sigma_bridge intent_context scenario_params"
fi
if [ "$OVD" = "1" ]; then
    BUILD_PKGS="$BUILD_PKGS intent_ovd"
fi
if [ "$ESTIMATOR_MODE" = "live" ]; then
    BUILD_PKGS="$BUILD_PKGS intent_confidence"
fi
log "빌드 ($BUILD_PKGS) (컨테이너: $CONTAINER) ..."
docker exec "$CONTAINER" bash -c "
    source /opt/ros_gz_ws/install/setup.bash
    cd /workspace
    colcon build --packages-select $BUILD_PKGS --symlink-install 2>&1 | tail -8
" || die "빌드 실패 ($BUILD_PKGS)"
log "    ✓ 빌드 완료"

# ------------------------------------------------------------------
# 로그 디렉토리
# ------------------------------------------------------------------
docker exec "$CONTAINER" bash -c "mkdir -p $LOG_DIR"

# ------------------------------------------------------------------
# context_graph_publisher 시작 (fusion mode 만 — 백그라운드)
# scenario 정적 장면(객체+좌표)을 /intent/context_graph 로 발행 → wrapper 가 구독.
# ------------------------------------------------------------------
if [ "$MODE" = "fusion" ]; then
    log "context_graph_publisher 시작 (scenario=$SCENARIO) ..."
    docker exec -d "$CONTAINER" bash -c "
        source /opt/ros_gz_ws/install/setup.bash
        source /workspace/install/setup.bash
        exec python3 /workspace/intent/context/intent_context/context_graph_publisher.py \
            --ros-args -p scenario:=$SCENARIO \
            > $LOG_DIR/context_graph.log 2>&1
    " 2>/dev/null || true
    sleep 1
fi

# ------------------------------------------------------------------
# wrapper_node 시작 (백그라운드)
# ------------------------------------------------------------------
log "wrapper_node 시작 (backbone=$BACKBONE, scenario=$SCENARIO, mode=$MODE, voice_lang=$VOICE_LANG) ..."
WRAPPER_CMD="
    source /opt/ros_gz_ws/install/setup.bash
    source /workspace/install/setup.bash
    export OLLAMA_BASE_URL=$OLLAMA_BASE_URL
    export VOICE_LANG=$VOICE_LANG
    exec python3 /workspace/intent/llm/intent_llm/wrapper_node.py \
        --ros-args \
        -p backbone:=$BACKBONE \
        -p scenario:=$SCENARIO \
        -p mode:=$MODE
"
if [ -n "${OPENAI_API_KEY:-}" ]; then
    WRAPPER_CMD="export OPENAI_API_KEY=$OPENAI_API_KEY; $WRAPPER_CMD"
fi

docker exec -d "$CONTAINER" bash -c "
    $WRAPPER_CMD > $LOG_DIR/wrapper_node.log 2>&1
" 2>/dev/null || true
# wrapper_node 기동 대기
sleep 2

# ------------------------------------------------------------------
# sigma_bridge_node 시작 (백그라운드)
# ------------------------------------------------------------------
log "sigma_bridge_node 시작 (user_guard_r=${USER_GUARD_RADIUS_M}, "\
"standoff=${TARGET_STANDOFF_M}, detour_arrival=${DETOUR_ARRIVAL_THRESHOLD_M}, "\
"takeoff_alt=${TAKEOFF_ALT_M}) ..."
docker exec -d "$CONTAINER" bash -c "
    source /opt/ros_gz_ws/install/setup.bash
    source /workspace/install/setup.bash
    export VOICE_LANG=$VOICE_LANG
    exec ros2 run intent_sigma_bridge sigma_bridge_node \
        --ros-args \
        -p scenario_id:=$SCENARIO \
        -p user_guard_radius_m:=$USER_GUARD_RADIUS_M \
        -p target_standoff_m:=$TARGET_STANDOFF_M \
        -p detour_arrival_threshold_m:=$DETOUR_ARRIVAL_THRESHOLD_M \
        -p takeoff_altitude_m:=$TAKEOFF_ALT_M \
        > $LOG_DIR/sigma_bridge.log 2>&1
" 2>/dev/null || true
sleep 1

# ------------------------------------------------------------------
# OVD detector_node 시작 (OVD=1 만 — 백그라운드)
# 추론 = Docker CPU (ADR-0024 D1 b). ultralytics 미설치면 시스템 pip 로 보완
# (openai 패턴과 동일 — Dockerfile §5.5 에 baked, 구이미지 호환용 폴백).
# numpy<2 동시 고정: cv_bridge 가 NumPy 1.x ABI (Dockerfile §5 주석).
# weight 는 /workspace/models/ovd/ 에 받음 (.gitignore 의 models/ — 재사용).
# ------------------------------------------------------------------
if [ "$OVD" = "1" ]; then
    if ! docker exec "$CONTAINER" python3 -c "import ultralytics" 2>/dev/null; then
        log "ultralytics 미설치 — 컨테이너에 설치 중 (torch CPU arm64 포함, 수 분) ..."
        docker exec "$CONTAINER" pip install --quiet 'numpy<2' \
            'torch>=2.6,<3.0' 'torchvision>=0.21,<0.22' 'ultralytics>=8.3,<9.0' \
            2>&1 | tail -3
    fi
    # YOLO-World set_classes 의 clip 의존 — ultralytics 자동 설치는 metadata
    # UNKNOWN 으로 깨짐 (실측 2026-06-11) → OpenAI 원본 명시 설치.
    if ! docker exec "$CONTAINER" python3 -c "import clip" 2>/dev/null; then
        log "clip 미설치 — 컨테이너에 설치 중 ..."
        docker exec "$CONTAINER" pip install --quiet \
            'git+https://github.com/openai/CLIP.git' 2>&1 | tail -2
    fi
    log "ovd_detector 시작 (device=cpu, throttle_hz=$OVD_THROTTLE_HZ, vocab=$OVD_VOCAB) ..."
    docker exec -d "$CONTAINER" bash -c "
        source /opt/ros_gz_ws/install/setup.bash
        source /workspace/install/setup.bash
        mkdir -p /workspace/models/ovd && cd /workspace/models/ovd
        exec ros2 launch intent_ovd ovd_detector.launch.py \
            device:=cpu \
            model_path:=yolov8s-worldv2.pt \
            vocabulary:=\"$OVD_VOCAB\" \
            throttle_hz:=$OVD_THROTTLE_HZ \
            > $LOG_DIR/ovd_detector.log 2>&1
    " 2>/dev/null || true
    sleep 1
fi

# ------------------------------------------------------------------
# Estimator live 모드 시작 (ESTIMATOR_MODE=live 만 — 백그라운드)
# P2 strict e2e: OVD detections(s1) + wrapper sigma_raw(s2/s3) → c̃ →
# /intent/grounding_confidence (tier1 b2 구독). ADR-0020 Amendment 2026-05-31.
# 토픽 미수신·신호 부재 → raw c=0 fail-safe (D3).
# ------------------------------------------------------------------
if [ "$ESTIMATOR_MODE" = "live" ]; then
    log "estimator_node[live] 시작 (dot_c_max=$ESTIMATOR_DOT_C_MAX, sigma_latch=${ESTIMATOR_SIGMA_LATCH_S}s, s1←OVD, s2/s3←sigma_raw) ..."
    if [ "$OVD" != "1" ]; then
        log "  ⚠ OVD=1 미동반 — s1 source 부재로 c̃→0 (fail-safe). 정상 c̃ 산출엔 OVD=1 권장."
    fi
    docker exec -d "$CONTAINER" bash -c "
        source /opt/ros_gz_ws/install/setup.bash
        source /workspace/install/setup.bash
        exec ros2 launch intent_confidence estimator.launch.py \
            estimator_mode:=live \
            dot_c_max:=$ESTIMATOR_DOT_C_MAX \
            sigma_latch_timeout_s:=$ESTIMATOR_SIGMA_LATCH_S \
            > $LOG_DIR/estimator.log 2>&1
    " 2>/dev/null || true
    sleep 1
fi

# ------------------------------------------------------------------
# 결과 확인
# ------------------------------------------------------------------
log ""
log "✓ 의도 스택 시작됨"
log "  backbone  : $BACKBONE"
log "  scenario  : $SCENARIO"
log "  container : $CONTAINER"
log "  sigma_bridge guards:"
log "    user_guard_radius_m       : $USER_GUARD_RADIUS_M (override: USER_GUARD_RADIUS_M=...)"
log "    target_standoff_m         : $TARGET_STANDOFF_M (override: TARGET_STANDOFF_M=...)"
log "    detour_arrival_threshold_m: $DETOUR_ARRIVAL_THRESHOLD_M (override: DETOUR_ARRIVAL_THRESHOLD_M=...)"
log "    takeoff_altitude_m (z floor): $TAKEOFF_ALT_M (override: TAKEOFF_ALT_M=...; 0=비활성)"
log ""
log "ROS 노드 확인:"
docker exec "$CONTAINER" bash -c "
    source /opt/ros_gz_ws/install/setup.bash
    ros2 node list 2>/dev/null
" 2>&1 || true
log ""
log "로그 확인:"
log "  docker exec $CONTAINER bash -c \"tail -f $LOG_DIR/wrapper_node.log\""
log "  docker exec $CONTAINER bash -c \"tail -f $LOG_DIR/sigma_bridge.log\""
log ""
log "테스트 (임의 utterance 발행 — -w 2 로 wrapper+sigma_bridge discovery 대기):"
log "  docker exec -e _STT_TEXT='앞으로 가줘' $CONTAINER /ros_entrypoint.sh bash -c \\"
log "    \"ros2 topic pub --once -w 2 /intent/user_prompt_raw std_msgs/msg/String \\\"data: '\$_STT_TEXT'\\\"\""
log ""
log "종료하려면 Ctrl+C (또는 아래 명령):"
log "  docker exec $CONTAINER bash -c \"pkill -f wrapper_node || true; pkill -f sigma_bridge || true\""
log ""

# 로그 스트리밍 (Ctrl+C 까지)
cleanup() {
    log "종료 — 의도 스택 노드 정지 ..."
    docker exec "$CONTAINER" bash -c "
        pkill -f wrapper_node.py 2>/dev/null || true
        pkill -f sigma_bridge_node.py 2>/dev/null || true
        pkill -f context_graph_publisher.py 2>/dev/null || true
        pkill -f 'intent_ovd' 2>/dev/null || true
        pkill -f detector_node 2>/dev/null || true
        pkill -f estimator_node 2>/dev/null || true
        sleep 1
        source /opt/ros_gz_ws/install/setup.bash; fastdds shm clean >/dev/null 2>&1 || true
    " 2>/dev/null || true
}
trap cleanup EXIT INT TERM

STREAM_LOGS="$LOG_DIR/wrapper_node.log $LOG_DIR/sigma_bridge.log"
if [ "$MODE" = "fusion" ]; then
    STREAM_LOGS="$STREAM_LOGS $LOG_DIR/context_graph.log"
fi
if [ "$OVD" = "1" ]; then
    STREAM_LOGS="$STREAM_LOGS $LOG_DIR/ovd_detector.log"
fi
if [ "$ESTIMATOR_MODE" = "live" ]; then
    STREAM_LOGS="$STREAM_LOGS $LOG_DIR/estimator.log"
fi

log "로그 스트리밍 (Ctrl+C 로 종료) ..."
docker exec "$CONTAINER" bash -c "
    tail -f $STREAM_LOGS 2>/dev/null
" || true
