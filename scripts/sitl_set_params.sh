#!/usr/bin/env bash
# sitl_set_params.sh — PX4 SITL에 preflight 완화 파라미터 자동 적용 (mavlink).
#
# 매번 T1 콘솔에 손으로 입력하던 4건을 mavlink_shell로 자동 send. up.sh가
# 부팅 후 호출하지만, 단독으로도 실행 가능 (예: T1 재시작 후 param만 reset).
#
# 전제:
#   - T1 PX4 SITL이 가동 중 (Ready for takeoff! 도달 후 ≥5초)
#   - ~/PX4-Autopilot/.venv에 pymavlink 설치 (PX4 toolchain venv가 자동 보유)
#   - PX4 mavlink가 14540 포트로 listen (SITL default)
#
# 적용 파라미터:
#   NAV_DLL_ACT=0       Data link loss action = disabled (GCS 미연결 SITL 환경)
#   NAV_RCL_ACT=0       RC loss action = disabled
#   COM_RCL_EXCEPT=4    RC loss exception bit — OFFBOARD 중엔 RC loss 무시
#                       (G1 트랙은 RC 없음)
#   --- tier0 geofence (ADR-0029 블로커 3 / B5 tier0 펌웨어 failsafe) ---
#   GF_ACTION=2         경계 침범 시 Hold (1=warn 2=hold 3=RTL 5=land). tier0 =
#                       LLM 불가지·우회불가 환경 containment. 적대 nominal 이
#                       tier1 사용자 회피 영역을 접선으로 미끄러져 방을 벗어나는
#                       경로(세션 44 S6 탈주)를 펌웨어 계층이 차단.
#   GF_MAX_HOR_DIST     home(이륙 dock) 기준 최대 수평 거리 [m] (env GF_MAX_HOR_DIST,
#                       기본 4.0 = livingroom 방 containment). yard 는 더 큰 값.
#   GF_MAX_VER_DIST     최대 고도 [m] (env GF_MAX_VER_DIST, 기본 3.0).
# tier1(사용자 회피 영역, 연속 CBF) ↔ tier0(환경 경계, 펌웨어) 분업 (B5 3-tier).
#
# 참고: px4vision_indoor airframe(sim/px4_overlay/22000_gz_px4vision_indoor)은
# EKF2 sensor flag + MPC_THR_HOVER 보정을 boot 시점에 적용하므로, 여기서 runtime
# override 불필요. Runtime param 변경은 EKF2 내부 상태를 reset하지 않아 잘 안 먹음
# (ADR-0012 §Incident 참조).
#
# Mag/heading 관련 param은 SDF spherical_coordinates fix로 불필요 (Task #7
# 완료 후 PX4 default로 preflight 자동 통과).

set -euo pipefail

PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
MAVLINK_PORT="${MAVLINK_PORT:-14540}"
TIMEOUT_S="${TIMEOUT_S:-30}"
# tier0 geofence 경계 (ADR-0029 블로커 3). 환경별 override.
GF_MAX_HOR_DIST="${GF_MAX_HOR_DIST:-4.0}"
GF_MAX_VER_DIST="${GF_MAX_VER_DIST:-3.0}"
GF_ACTION="${GF_ACTION:-2}"

if [ ! -d "$PX4_DIR" ]; then
  echo "ERROR: PX4-Autopilot not found at $PX4_DIR" >&2
  exit 1
fi

# PX4 venv 사용 (pymavlink 보유). ADR-0004 위반 아님 — PX4 toolchain venv는
# 별 venv. (리포 .venv에도 pymavlink 추가 가능하나 PX4 venv 재활용이 단순.)
if [ ! -f "$PX4_DIR/.venv/bin/activate" ]; then
  echo "ERROR: PX4 venv not found at $PX4_DIR/.venv" >&2
  echo "       Run scripts/setup_native_macos.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$PX4_DIR/.venv/bin/activate"

python3 - <<PYEOF
import sys
import time

try:
    from pymavlink import mavutil
except ImportError:
    print("pymavlink 미설치 — PX4 toolchain venv 확인 필요", file=sys.stderr)
    sys.exit(1)

PORT = $MAVLINK_PORT
TIMEOUT_S = $TIMEOUT_S
GF_MAX_HOR_DIST = $GF_MAX_HOR_DIST
GF_MAX_VER_DIST = $GF_MAX_VER_DIST
GF_ACTION = $GF_ACTION

print(f"[SITL params] mavlink 연결 시도 (udp:0.0.0.0:{PORT}) ...")
master = mavutil.mavlink_connection(f'udp:0.0.0.0:{PORT}', source_system=255)

# Heartbeat 대기 — PX4 부팅 + SITL ready 신호.
hb = master.wait_heartbeat(timeout=TIMEOUT_S)
if hb is None:
    print(f"[SITL params] FAIL — heartbeat 미수신 ({TIMEOUT_S}s 타임아웃)", file=sys.stderr)
    sys.exit(2)
print(f"[SITL params] heartbeat OK (sys={master.target_system} comp={master.target_component})")

PARAMS = [
    ('NAV_DLL_ACT',       0,   mavutil.mavlink.MAV_PARAM_TYPE_INT32),
    ('NAV_RCL_ACT',       0,   mavutil.mavlink.MAV_PARAM_TYPE_INT32),
    ('COM_RCL_EXCEPT',    4,   mavutil.mavlink.MAV_PARAM_TYPE_INT32),
    # XY velocity P-게인: 기본 1.8(τ≈0.56s) → 4.0(τ≈0.25s).
    # Z축과 동일한 값. 2s 명령 추적률 60% → 87% 목표.
    # airframe default도 4.0으로 맞췄으나 ROMFS rebuild 전까지 이 설정이 유효.
    ('MPC_XY_VEL_P_ACC',  4.0, mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
    # tier0 geofence (ADR-0029 블로커 3) — 환경 경계 containment.
    ('GF_ACTION',         GF_ACTION,       mavutil.mavlink.MAV_PARAM_TYPE_INT32),
    ('GF_MAX_HOR_DIST',   GF_MAX_HOR_DIST, mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
    ('GF_MAX_VER_DIST',   GF_MAX_VER_DIST, mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
]

for name, value, ptype in PARAMS:
    master.mav.param_set_send(
        master.target_system,
        master.target_component,
        name.encode('ascii'),
        float(value),
        ptype,
    )
    # PARAM_VALUE ack 대기 (PX4가 적용 확인 emit).
    msg = master.recv_match(type='PARAM_VALUE', blocking=True, timeout=3)
    if msg and msg.param_id == name:
        print(f"  [PASS] {name} = {value} (ack)")
    else:
        print(f"  [WARN] {name} = {value} (ack 미수신 — 적용은 됐을 가능성)")

print("[SITL params] 완료.")
PYEOF
