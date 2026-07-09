#!/usr/bin/env bash
# install_docker_desktop_macos.sh — macOS Apple Silicon용 Docker Desktop 설치 (1회, 멱등)
#
# 목적: Sim 트랙 E2 진입 전 MacBook에 Docker Desktop을 설치한다.
# 환경: macOS arm64 (Apple Silicon) + Homebrew
#
# 사용:
#   bash scripts/install_docker_desktop_macos.sh
#
# 설치 후 사용자가 직접 수행해야 할 5단계:
#   1. Spotlight(Cmd+Space) → 'Docker' → Docker Desktop 실행
#   2. 라이선스 동의.
#      ※ macOS 14+(Sonoma/Sequoia)에서는 '시스템 소프트웨어' 승인 항목이 없을 수 있음 — 정상.
#        Docker Desktop 4.x는 Apple Virtualization Framework를 사용해 커널 확장 불필요.
#   3. 메뉴바 고래 아이콘이 "Docker Desktop is running" 상태가 될 때까지 대기
#   4. Docker Desktop 설정 > Resources > Memory: 12 GiB 이상으로 설정
#      (기본 ~7.75 GiB로는 ros_gz 소스 빌드 중 OOM 발생)
#   5. Resource Saver 비활성화 (5분 idle 후 VM 정지 → docker run 500 오류 유발):
#      curl -s --unix-socket ~/Library/Containers/com.docker.docker/Data/backend.sock \
#        -X POST http://localhost/app/settings -H 'Content-Type: application/json' \
#        -d '{"useResourceSaver": false}' > /dev/null && \
#      curl -s --unix-socket ~/Library/Containers/com.docker.docker/Data/backend.sock \
#        -X POST http://localhost/engine/restart > /dev/null

set -euo pipefail

# --------------------------------------------------------------------------
# 1. 이미 설치되어 있으면 버전만 출력 후 종료
# --------------------------------------------------------------------------
if [ -d "/Applications/Docker.app" ]; then
    echo "[OK] Docker Desktop 이미 설치되어 있음."
    if command -v docker &>/dev/null; then
        docker --version
        if docker info &>/dev/null 2>&1; then
            echo "[OK] Docker Desktop 실행 중."
        else
            echo "[주의] Docker Desktop 앱은 있으나 아직 시작되지 않았습니다."
            echo "       Docker Desktop을 실행하고 메뉴바 고래 아이콘이 뜰 때까지 기다리세요."
        fi
    else
        echo "[주의] /Applications/Docker.app 은 있으나 docker CLI가 PATH에 없습니다."
        echo "       Docker Desktop을 한 번 실행한 뒤 새 터미널을 여세요."
    fi
    exit 0
fi

# --------------------------------------------------------------------------
# 2. Homebrew 확인
# --------------------------------------------------------------------------
if ! command -v brew &>/dev/null; then
    echo "[ERROR] Homebrew가 설치되어 있지 않습니다."
    echo "  https://brew.sh 에서 먼저 설치한 뒤 다시 실행하세요."
    exit 1
fi
echo "[INFO] Homebrew $(brew --version | head -1) 확인."

# --------------------------------------------------------------------------
# 3. 아키텍처 확인 (Apple Silicon 전용)
# --------------------------------------------------------------------------
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
    echo "[ERROR] 이 스크립트는 Apple Silicon(arm64) 전용입니다. 현재 아키텍처: $ARCH"
    exit 1
fi

# --------------------------------------------------------------------------
# 4. /usr/local/cli-plugins 사전 생성 (brew --cask docker 선행 조건)
#    brew cask가 내부적으로 `sudo mkdir /usr/local/cli-plugins`를 시도하는데,
#    비대화 터미널에서는 sudo 패스워드 프롬프트가 막혀 실패한다.
#    디렉터리를 현재 사용자 소유로 미리 만들어두면 brew가 sudo 없이 진행 가능.
# --------------------------------------------------------------------------
## brew cask docker 는 /usr/local/{cli-plugins,bin} 두 곳에 파일을 써야 한다.
## /usr/local 이 root:wheel(755)이라 비대화 sudo 가 막히므로, osascript 인증
## 창으로 한 번에 두 디렉터리 소유권을 현재 사용자로 바꿔 brew 가 sudo 없이 진행.
_ME="$(whoami)"
_NEED_FIX=0
[ ! -d "/usr/local/cli-plugins" ] && _NEED_FIX=1
# /usr/local/bin 이 root 소유이고 현재 사용자가 쓸 수 없으면 fix 필요
if [ -d "/usr/local/bin" ] && [ ! -w "/usr/local/bin" ]; then _NEED_FIX=1; fi

if [ "$_NEED_FIX" = "1" ]; then
    echo "[INFO] /usr/local/{cli-plugins,bin} 권한 조정 중"
    echo "       (관리자 권한 필요 — macOS 인증 창이 뜹니다) ..."
    osascript -e "do shell script \
        \"mkdir -p /usr/local/cli-plugins && \
          chown ${_ME}:staff /usr/local/cli-plugins && \
          chown ${_ME}:staff /usr/local/bin\" \
        with administrator privileges"
    echo "[OK] 권한 조정 완료."
else
    echo "[OK] /usr/local/{cli-plugins,bin} 이미 쓰기 가능."
fi

# --------------------------------------------------------------------------
# 5. Docker Desktop 설치 (Homebrew Cask)
# --------------------------------------------------------------------------
echo "[INFO] Docker Desktop 설치 중 (brew install --cask docker) ..."
echo "       완료까지 수 분 소요될 수 있습니다."
echo ""

# Homebrew 자동 업데이트는 시간이 오래 걸릴 수 있으므로 비활성화
HOMEBREW_NO_AUTO_UPDATE=1 brew install --cask docker

# --------------------------------------------------------------------------
# 5. 설치 확인 및 안내
# --------------------------------------------------------------------------
if [ ! -d "/Applications/Docker.app" ]; then
    echo "[ERROR] 설치 후에도 /Applications/Docker.app 을 찾을 수 없습니다."
    echo "       brew install --cask docker 출력을 확인하세요."
    exit 1
fi

echo ""
echo "=========================================================="
echo " Docker Desktop 설치 완료!"
echo "=========================================================="
echo ""
echo "▶ 지금 바로 해야 할 단계:"
echo ""
echo "  [1] Spotlight(Cmd+Space) → 'Docker' 검색 → Docker Desktop 실행"
echo ""
echo "  [2] 최초 실행 시 라이선스 동의 창이 뜨면 동의"
echo "      ※ macOS 14+ (Sonoma/Sequoia)에서는 '시스템 소프트웨어' 목록에"
echo "        Docker 항목이 보이지 않을 수 있음 — 정상. 승인 불필요."
echo "        (Docker Desktop 4.x가 Apple Virtualization Framework를 사용하므로"
echo "         커널 확장이 없음)"
echo ""
echo "  [3] 메뉴바 상단 고래(🐳) 아이콘 클릭 →"
echo "      'Docker Desktop is running' 상태 확인"
echo ""
echo "  [4] Docker Desktop 메뉴바 아이콘 > Settings >"
echo "      Resources > Memory 슬라이더를 12 GiB 이상으로 변경 후 Apply"
echo "      (기본 ~7.75 GiB로는 ros_gz 소스 빌드 중 OOM 발생)"
echo ""
echo "  [5] Docker Desktop 실행 후 아래 명령으로 Resource Saver를 비활성화:"
echo "      (Resource Saver는 5분 idle 후 VM을 정지시켜"
echo "       'docker run' 500 오류를 유발함)"
echo ""
echo "      curl -s --unix-socket \\"
echo "        ~/Library/Containers/com.docker.docker/Data/backend.sock \\"
echo "        -X POST http://localhost/app/settings \\"
echo "        -H 'Content-Type: application/json' \\"
echo "        -d '{\"useResourceSaver\": false}' > /dev/null && \\"
echo "      curl -s --unix-socket \\"
echo "        ~/Library/Containers/com.docker.docker/Data/backend.sock \\"
echo "        -X POST http://localhost/engine/restart > /dev/null && \\"
echo "      echo 'Resource Saver 비활성화 완료'"
echo ""
echo "▶ Docker Desktop 실행 확인 후 이미지를 빌드합니다:"
echo ""
echo "  docker buildx build --platform linux/arm64 \\"
echo "      -t llmdrone-sim:latest -f docker/Dockerfile ."
echo ""
echo "  예상 빌드 시간: 30–50분 (MicroXRCEAgent 포함, M 시리즈 기준)"
echo ""
