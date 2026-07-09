#!/usr/bin/env bash
# build_microxrce_agent_macos.sh — macOS 26 Tahoe + cmake 4.x 환경에서
# MicroXRCEAgent (eProsima/Micro-XRCE-DDS-Agent) 소스 빌드 자동화.
#
# 결과물: ~/MicroXRCEAgent/build/MicroXRCEAgent
#
# 전제조건 (one-time):
#   - Homebrew 설치
#   - Xcode Command Line Tools (`xcode-select --install`)
#   - cmake, ninja (`brew install cmake ninja`)
#
# 배경 (ADR-0010):
#   macOS 26 Tahoe + AppleClang 21 + cmake 4.x에서 라이브러리 구식 코드와
#   새 툴체인 충돌 4건 — 이 스크립트가 모두 자동 처리함.
#
# 사용:
#   bash scripts/build_microxrce_agent_macos.sh
#   # 빌드 완료 후: ~/MicroXRCEAgent/build/MicroXRCEAgent 바이너리 생성
#   # 에이전트 실행: ~/MicroXRCEAgent/build/MicroXRCEAgent udp4 -p 8888

set -euo pipefail

AGENT_DIR="$HOME/MicroXRCEAgent"
BUILD_DIR="$AGENT_DIR/build"
SYSROOT="$(xcrun --show-sdk-path)"

log() { echo "[build_microxrce_agent_macos] $*"; }

# ── 1. macOS 26 Tahoe: libc++ 헤더가 SDK 내부로 이동 ─────────────────────
# CLT 21의 /usr/include/c++/v1/에는 __cxx_version만 있고 표준 헤더가 없음.
# SDK 경로를 CPLUS_INCLUDE_PATH로 주입해 cmake check_cxx_source_compiles가
# 통과하도록 한다.
_clt_cxx="/Library/Developer/CommandLineTools/usr/include/c++/v1"
_sdk_cxx="${SYSROOT}/usr/include/c++/v1"
if [ ! -f "${_clt_cxx}/atomic" ] && [ -f "${_sdk_cxx}/atomic" ]; then
    export CPLUS_INCLUDE_PATH="${_sdk_cxx}${CPLUS_INCLUDE_PATH:+:${CPLUS_INCLUDE_PATH}}"
    log "macOS 26 Tahoe: CPLUS_INCLUDE_PATH → ${_sdk_cxx}"
fi

# ── 2. 클론 (이미 있으면 skip) ────────────────────────────────────────────
if [ ! -d "$AGENT_DIR/.git" ]; then
    log "클론: eProsima/Micro-XRCE-DDS-Agent → $AGENT_DIR"
    git clone https://github.com/eProsima/Micro-XRCE-DDS-Agent.git "$AGENT_DIR"
else
    log "기존 클론 사용: $AGENT_DIR"
fi

# ── 3. SuperBuild.cmake 패치 (idempotent) ────────────────────────────────
SUPERBUILD="$AGENT_DIR/cmake/SuperBuild.cmake"

# 패치 A: macOS sysroot를 ExternalProject 서브 빌드에 전파.
#   foonathan_memory 구성 시 부모 CMAKE_OSX_SYSROOT를 상속 못해 <cstddef> 미발견.
# 멱등성 마커: 삽입된 매크로 블록의 첫 주석 줄을 단일 라인 grep으로 검출.
# (이전엔 다중 키워드 정규식이라 grep이 다중 행을 매칭 못해 매 실행마다 재삽입됐음.)
if ! grep -qF "macOS: CMAKE_OSX_SYSROOT를 모든 ExternalProject" "$SUPERBUILD"; then
    log "패치 A: SuperBuild macOS sysroot 전파 추가"
    # Android if 블록 직후에 macOS 블록 삽입
    sed -i.bak '/^if(ANDROID)/,/^endif()/{
/^endif()$/a\
\
# macOS: CMAKE_OSX_SYSROOT를 모든 ExternalProject 서브 빌드에 전파.\
# CMake 4.x + AppleClang 15+에서 서브 프로젝트가 sysroot를 상속 못해\
# <cstddef> 등 표준 헤더를 못 찾는 문제 우회 (ADR-0010, 2026-05-23).\
if(APPLE AND CMAKE_OSX_SYSROOT)\
    list(APPEND CROSS_CMAKE_ARGS\
        -DCMAKE_OSX_SYSROOT:PATH=${CMAKE_OSX_SYSROOT}\
    )\
endif()
}' "$SUPERBUILD"
fi

# 패치 B: fastdds AppleClang 21 -Wnonnull 우회.
#   TypeObjectRegistry.cpp:2620에서 null 포인터 → non-null 파라미터 경고가 fatal.
if ! grep -q "Wno-error=nonnull" "$SUPERBUILD"; then
    log "패치 B: fastdds -Wno-error=nonnull 추가"
    sed -i.bak 's/-DCOMPILE_TOOLS:BOOL=OFF/-DCOMPILE_TOOLS:BOOL=OFF\n                -DCMAKE_CXX_FLAGS:STRING=-Wno-error=nonnull/' "$SUPERBUILD"
fi

# sed -i.bak가 남긴 백업 정리 (위 두 패치 중 하나라도 적용됐다면 존재).
rm -f "$SUPERBUILD.bak"

# ── 4. 빌드 ──────────────────────────────────────────────────────────────
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

log "cmake 구성 (SOCKETCAN=OFF, sysroot=${SYSROOT})"
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -GNinja \
    -DCMAKE_OSX_SYSROOT="$SYSROOT" \
    -DUAGENT_SOCKETCAN_PROFILE=OFF

log "빌드 시작 (병렬: $(sysctl -n hw.logicalcpu) 코어)"
cmake --build . --parallel "$(sysctl -n hw.logicalcpu)"

# ── 5. 결과 확인 ─────────────────────────────────────────────────────────
AGENT_BIN="$BUILD_DIR/MicroXRCEAgent"
if [ -x "$AGENT_BIN" ]; then
    log "빌드 성공: $AGENT_BIN"
    log ""
    log "실행 예시:"
    log "  $AGENT_BIN udp4 -p 8888 -v6"
    log ""
    log "PX4 SITL을 실행하면 자동으로 localhost:8888에 연결합니다."
else
    log "ERROR: 빌드 산출물이 없습니다." >&2
    exit 1
fi
