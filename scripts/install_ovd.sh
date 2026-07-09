#!/usr/bin/env bash
# install_ovd.sh — paper-1 B1 (Open-Vocabulary Detection) 의존성 설치.
#
# 잠금 (ADR-0021 1차 답): YOLO-World 단일 백본 (ultralytics 경유), Apple
#       Silicon MPS 백엔드. Grounding DINO 는 paper-1 범위 밖.
#
# 정책 (ADR-0004): 모든 Python 은 $REPO_ROOT/.venv 안에서만.
# 정책 (2026-05-25 사용자 확정): .venv 는 Python 3.11 계열. 본 스크립트가 검사·재생성.
#
# 멱등 — 재실행 안전. 각 단계는 기존 상태를 먼저 확인.
#
# 단계
#   1) Preflight: macOS arm64 / brew 확인
#   2) Homebrew python@3.11 설치 (없을 때만)
#   3) .venv 검사·재생성 — 현재 .venv 가 3.11 이 아니면 backup 후 재생성 + requirements-dev 재설치
#   4) requirements-ovd.txt 설치 (torch / torchvision / ultralytics / opencv-python /
#      pillow / supervision / huggingface-hub)
#   5) intent/ovd editable install
#   6) (opt-in) 모델 weight pull — OVD_FETCH_WEIGHTS=1 일 때만
#   7) Verify: torch.mps / ultralytics import / intent_ovd import
#
# Env overrides
#   OVD_FETCH_WEIGHTS  1 이면 YOLO-World v2 weight 도 받음 (기본 0 — opt-in)
#   OVD_MODEL          fetch 대상 모델 (기본 yolov8s-worldv2.pt; 옵션: yolov8m-worldv2.pt 등)
#   FORCE_VENV_REBUILD 1 이면 .venv 가 이미 3.11 이어도 재생성 (디버깅용)
#   LOG_FILE           로그 경로 (기본 $REPO_ROOT/install_ovd.log)
#   VERBOSE            1 이면 bash xtrace
#
# Usage
#   ./scripts/install_ovd.sh                          # 의존성만
#   OVD_FETCH_WEIGHTS=1 ./scripts/install_ovd.sh      # weight 도 받기
#   OVD_FETCH_WEIGHTS=1 OVD_MODEL=yolov8m-worldv2.pt ./scripts/install_ovd.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${LOG_FILE:-$REPO_ROOT/install_ovd.log}"
OVD_FETCH_WEIGHTS="${OVD_FETCH_WEIGHTS:-0}"
OVD_MODEL="${OVD_MODEL:-yolov8s-worldv2.pt}"
FORCE_VENV_REBUILD="${FORCE_VENV_REBUILD:-0}"
REQUIRED_PY_MAJOR_MINOR="3.11"

# Homebrew auto-update 가 중간에 끼면 brew list 검사가 race — 끈다.
export HOMEBREW_NO_AUTO_UPDATE=1

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

#-------------------------------------------------------------------------------
# Logging (setup_native_macos.sh 와 동일 helper)
#-------------------------------------------------------------------------------
ts()      { date '+%Y-%m-%d %H:%M:%S'; }
log()     { printf '[%s] [install_ovd] %s\n' "$(ts)" "$*"; }
warn()    { printf '[%s] [install_ovd] WARN: %s\n' "$(ts)" "$*"; }
die()     { printf '[%s] [install_ovd] ERROR: %s\n' "$(ts)" "$*"; exit 1; }
section() { printf '\n[%s] ========== %s ==========\n' "$(ts)" "$*"; }

on_err() {
  local rc=$?
  printf '\n[%s] [install_ovd] FAILED rc=%d at line %d: %s\n' \
    "$(ts)" "$rc" "${BASH_LINENO[0]}" "$BASH_COMMAND"
  printf '[install_ovd] Full log: %s\n' "$LOG_FILE"
  printf '[install_ovd] When reporting, share the last ~50 lines of that file.\n'
  sync; sleep 0.5
  exit "$rc"
}
trap on_err ERR

on_exit() {
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '\n[%s] [install_ovd] SUCCESS. Full log: %s\n' "$(ts)" "$LOG_FILE"
  fi
}
trap on_exit EXIT

[ "${VERBOSE:-0}" = "1" ] && set -x

#-------------------------------------------------------------------------------
# Step 1/7: Preflight
#-------------------------------------------------------------------------------
section "Step 1/7: Preflight"

log "Repo:        $REPO_ROOT"
log "Host:        $(uname -s) $(uname -m) ($(sw_vers -productVersion 2>/dev/null || echo 'non-macOS'))"

[ "$(uname -s)" = "Darwin" ]  || die "macOS only (uname=$(uname -s)). 다른 호스트는 별 트랙."
[ "$(uname -m)" = "arm64" ]   || die "Apple Silicon (arm64) only (uname -m=$(uname -m))."

command -v brew >/dev/null   || die "Homebrew not found. https://brew.sh 에서 먼저 설치."
BREW_PREFIX="$(brew --prefix)"
log "Homebrew:    $BREW_PREFIX"

#-------------------------------------------------------------------------------
# Step 2/7: Homebrew python@3.11
#-------------------------------------------------------------------------------
section "Step 2/7: Homebrew python@3.11"

if brew list python@3.11 >/dev/null 2>&1; then
  log "python@3.11: already installed ($(brew list --versions python@3.11 | head -1))"
else
  log "Installing Homebrew python@3.11 ..."
  brew install python@3.11
fi

PY311="$BREW_PREFIX/opt/python@3.11/bin/python3.11"
[ -x "$PY311" ] || die "python@3.11 not found at $PY311 — brew install 실패?"
log "Python 3.11:  $("$PY311" --version 2>&1) ($PY311)"

#-------------------------------------------------------------------------------
# Step 3/7: .venv 검사·재생성 (3.11 이 아니면 backup 후 재생성)
#-------------------------------------------------------------------------------
section "Step 3/7: .venv (Python 3.11 강제)"

PROJECT_VENV="$REPO_ROOT/.venv"
needs_rebuild=0
current_py_version=""

if [ ! -d "$PROJECT_VENV" ]; then
  log ".venv 없음 — 신규 생성 예정."
  needs_rebuild=1
else
  if [ -x "$PROJECT_VENV/bin/python" ]; then
    current_py_version="$("$PROJECT_VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo 'unknown')"
    log "현재 .venv Python: $current_py_version"
    if [ "$current_py_version" != "$REQUIRED_PY_MAJOR_MINOR" ]; then
      log ".venv 가 $REQUIRED_PY_MAJOR_MINOR 아님 ($current_py_version) — 재생성 예정."
      needs_rebuild=1
    elif [ "$FORCE_VENV_REBUILD" = "1" ]; then
      log "FORCE_VENV_REBUILD=1 — 재생성 예정."
      needs_rebuild=1
    else
      log ".venv 이미 Python $REQUIRED_PY_MAJOR_MINOR — 재생성 생략."
    fi
  else
    log ".venv/bin/python 실행 불가 — 재생성 예정."
    needs_rebuild=1
  fi
fi

if [ "$needs_rebuild" = "1" ]; then
  if [ -d "$PROJECT_VENV" ]; then
    backup_path="$PROJECT_VENV.backup-$(date +%Y%m%d-%H%M%S)"
    log "기존 .venv → $backup_path 로 이동 (안전 백업; .gitignore: .venv.backup-*/)"
    mv "$PROJECT_VENV" "$backup_path"
  fi
  # 이전 venv 의 pip wheel cache 는 새 Python 버전과 ABI mismatch — fresh download 가
  # 일어나면서 "Cache entry deserialization failed" 경고가 매번 뜸. 미리 비워서
  # 로그 노이즈 제거.
  if [ -d "$HOME/Library/Caches/pip" ]; then
    log "pip wheel cache 정리 (ABI mismatch 노이즈 회피)..."
    rm -rf "$HOME/Library/Caches/pip"
  fi
  log "Python $REQUIRED_PY_MAJOR_MINOR 로 .venv 생성 (--system-site-packages, ADR-0004)..."
  "$PY311" -m venv --system-site-packages "$PROJECT_VENV"
fi

# shellcheck disable=SC1091
source "$PROJECT_VENV/bin/activate"
log "venv:        $PROJECT_VENV"
log "Python:      $(python --version 2>&1) ($(which python))"
log "pip:         $(pip --version 2>&1 | head -1)"

# 재생성된 경우 — 기존 dev 의존성 (pytest 등) 복구.
if [ "$needs_rebuild" = "1" ]; then
  log "pip 업그레이드 + requirements-dev.txt 재설치 ..."
  pip install --quiet --upgrade pip
  if [ -f "$REPO_ROOT/requirements-dev.txt" ]; then
    pip install --quiet -r "$REPO_ROOT/requirements-dev.txt"
  fi
fi

#-------------------------------------------------------------------------------
# Step 4/7: requirements-ovd.txt 설치
#-------------------------------------------------------------------------------
section "Step 4/7: requirements-ovd.txt 설치 (torch / ultralytics / cv2 / supervision)"

REQ_FILE="$REPO_ROOT/requirements-ovd.txt"
[ -f "$REQ_FILE" ] || die "$REQ_FILE 가 없습니다 — 누락 또는 잘못된 repo state."

if [ "$needs_rebuild" != "1" ]; then
  log "pip 업그레이드..."
  pip install --quiet --upgrade pip
fi

# torch 는 wheel 크기가 큼 (>250MB) — 진행 표시를 위해 --quiet 제거.
log "ML 의존성 설치 중 (torch wheel ~250MB, ultralytics 등 ~200MB 추가; 첫 실행 5–10 분)..."
pip install -r "$REQ_FILE"

#-------------------------------------------------------------------------------
# Step 5/7: intent/ovd editable install
#-------------------------------------------------------------------------------
section "Step 5/7: intent/ovd editable install"

OVD_PKG="$REPO_ROOT/intent/ovd"
if [ ! -f "$OVD_PKG/setup.py" ]; then
  die "$OVD_PKG/setup.py 가 없습니다 — 패키지 스캐폴드 누락."
fi

log "pip install -e $OVD_PKG ..."
pip install -e "$OVD_PKG"

#-------------------------------------------------------------------------------
# Step 6/7: (opt-in) 모델 weight pull
#-------------------------------------------------------------------------------
section "Step 6/7: 모델 weight (OVD_FETCH_WEIGHTS=$OVD_FETCH_WEIGHTS)"

WEIGHT_DIR="$REPO_ROOT/models/ovd"
if [ "$OVD_FETCH_WEIGHTS" = "1" ]; then
  mkdir -p "$WEIGHT_DIR"
  WEIGHT_PATH="$WEIGHT_DIR/$OVD_MODEL"
  if [ -f "$WEIGHT_PATH" ]; then
    log "Weight already present: $WEIGHT_PATH ($(du -h "$WEIGHT_PATH" | cut -f1))"
  else
    log "Fetching $OVD_MODEL → $WEIGHT_PATH ..."
    # ultralytics 의 YOLO('name') 생성자가 자동 다운로드 (assets release 에서 받음).
    # cwd 가 weight 저장 위치가 되므로 일시 디렉터리 변경.
    (
      cd "$WEIGHT_DIR"
      python -c "
import os, sys
from ultralytics import YOLO
model_name = '$OVD_MODEL'
print(f'[install_ovd] downloading {model_name} via ultralytics ...', flush=True)
m = YOLO(model_name)
# 모델 weight 의 실 경로 확인 (cwd 또는 ultralytics cache).
ckpt = getattr(m, 'ckpt_path', None) or getattr(m, 'pt_path', None)
if ckpt and os.path.isfile(ckpt) and os.path.abspath(ckpt) != os.path.abspath(model_name):
    import shutil
    shutil.copy(ckpt, model_name)
print(f'[install_ovd] OK: {model_name}', flush=True)
"
    )
    log "Weight: $WEIGHT_PATH ($(du -h "$WEIGHT_PATH" | cut -f1))"
  fi
else
  log "Skip (opt-in). weight 도 받으려면:"
  log "  OVD_FETCH_WEIGHTS=1 $0"
fi

#-------------------------------------------------------------------------------
# Step 7/7: Verify
#-------------------------------------------------------------------------------
section "Step 7/7: Verify"

python - <<'PY'
import sys

ok = True

def check(name, fn):
    global ok
    try:
        v = fn()
        print(f'  ✓ {name}: {v}')
    except Exception as e:
        ok = False
        print(f'  ✗ {name} FAILED: {type(e).__name__}: {e}')

check('torch import',         lambda: __import__('torch').__version__)
check('torch MPS available',  lambda: __import__('torch').backends.mps.is_available())
check('torchvision import',   lambda: __import__('torchvision').__version__)
check('cv2 import',           lambda: __import__('cv2').__version__)
check('PIL import',           lambda: __import__('PIL').__version__)
check('ultralytics import',   lambda: __import__('ultralytics').__version__)
check('supervision import',   lambda: __import__('supervision').__version__)
check('huggingface_hub',      lambda: __import__('huggingface_hub').__version__)
check('intent_ovd import',    lambda: __import__('intent_ovd').__version__)

sys.exit(0 if ok else 1)
PY

log "Verify 통과."

#-------------------------------------------------------------------------------
# Next steps
#-------------------------------------------------------------------------------
section "Next steps"
cat <<EOF
OVD 의존성 + intent_ovd 스캐폴드 준비 완료.

활성 venv:    $PROJECT_VENV (Python $REQUIRED_PY_MAJOR_MINOR)
설치 산출물:  torch / torchvision / ultralytics / opencv-python / supervision /
              pillow / huggingface-hub + intent_ovd (editable)
Weight 경로:  $WEIGHT_DIR (받았다면 $WEIGHT_DIR/$OVD_MODEL)

venv 사용:
  source $PROJECT_VENV/bin/activate

다음 (ROADMAP §3 B1):
  - intent/ovd/intent_ovd/detector.py  — ultralytics YOLO wrapper (MPS 디스패치)
  - intent/ovd/intent_ovd/detector_node.py — ROS 2 노드
  - intent/ovd/test/test_detector.py — 헤드리스 단위 테스트
  - ADR-0021 (OVD 모델 선택) 정식 잠금
  - setup_native_macos.sh 의 PROJECT_PY 디폴트도 3.11 로 정합 (별 PR)
EOF
