#!/bin/bash
# run_native_sitl_yard.sh — PX4 SITL × yard_base.sdf on macOS host (native).
#
# Wrapper script — sim 인프라 *outdoor world (yard_base)* 호환성 점검용. sim/worlds/yard_base.sdf
# 측 ADR-0006 amendment 1+2 S8 정적 layout (가옥 + 사용자 휠체어 + 자녀 + 가족 5명
# capsule). paper §C scope 측 영향 *없음* — sim 인프라 점검 측면 측 *별 트랙*.
#
# ADR-0008 D1 측 native macOS 측 *기본* livingroom 검증 자리 → 본 wrapper 측 yard 측
# 확장 (별 ADR 또는 ADR-0008 amendment 후보).
#
# Prerequisites: scripts/run_native_sitl_livingroom.sh 측 동일 (PX4-Autopilot
# clone + venv + Homebrew gz-sim8 + D4 패치 3건).
#
# 사용 (두 터미널 패턴, ADR-0008 D3):
#   T1: bash scripts/run_native_sitl_yard.sh
#       → PX4 SITL + Gazebo server (HEADLESS=1, render_engine=ogre)
#   T2: export GZ_IP=127.0.0.1; gz sim -g
#       → GUI 측 별 Terminal.app 세션 측 시작 (--render-engine 미지정 = ogre2)
#
# Spawn pose (yard_base.sdf 측 dock 좌표 (0, -2, 0.025) 측 정합):
#   드론 측 dock 위 (0.0, -2.0, 0.15) 측 spawn — leg height ~0.125 m + 0.025 m
#   dock 표면 측 정합. yard layout 측 사용자 (0, -3, 1.1) 측 xy 거리 1.0 m,
#   r_min=0.9 m 측 *임계 근접* (1.0 m 측 r_min 측 약간 위, takeoff drift 가능성).
#   필요 시 PX4_GZ_MODEL_POSE override 측 dock 측 위 더 멀리 (예: (0.5, -2.0, 0.15)).
#
# Stop: Ctrl-C → PX4 shutdown 측 gz_bridge 측 gz 서버 정리. 잔재 시: pkill -f "gz sim".

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Yard layout 정합 env — script 측 livingroom default 측 yard override.
export WORLD_NAME=yard_base

# Spawn pose 측 yard dock (0, -2, 0.025) 위 드론 leg height (~0.125 m).
# 사용자 (0, -3, 1.1) 측 xy 거리 1.0 m (r_min=0.9 측 약간 위 — 임계 근접).
# 필요 시 본 env override 측 더 멀리 (예: PX4_GZ_MODEL_POSE="0.5,-2.0,0.15,0,0,0").
export PX4_GZ_MODEL_POSE="${PX4_GZ_MODEL_POSE:-0.0,-2.0,0.15,0,0,0}"

# PX4_SIM_MODEL 측 indoor 측 그대로 사용 — 드론 자체 측 outdoor 측 동일.
# outdoor 측 *별 모델* (예: GPS 측 stronger) 측 필요 시 env override.
# export PX4_SIM_MODEL=gz_px4vision_indoor  # default, livingroom.sh 측 정합.

# generic script 측 위임.
exec "$REPO_ROOT/scripts/run_native_sitl_livingroom.sh"
