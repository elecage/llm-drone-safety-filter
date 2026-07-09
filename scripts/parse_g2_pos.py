#!/usr/bin/env python3
# parse_g2_pos.py — /tmp/g2_pos.log (PX4 vehicle_local_position_v1 echo) 파서.
#
# run_g2_scenario.sh --diagnose가 호스트에서 docker cp로 컨테이너에 넣고
# 실행. (이전엔 `docker exec ... python3 - <<EOF` 인라인 heredoc를 썼는데
# 컨테이너 STDIN 전달 실패로 무음 종료 — docker cp 경유로 변경.)
#
# 환경변수:
#   POS_LOG   기본 /tmp/g2_pos.log
#   SAMPLE_S  기본 2.0 (몇 초마다 한 줄 출력할지)
#   USER_POS  옵션 "x,y,z" (ENU). 주어지면 매 샘플에 3D 거리 + 최소 거리 요약.

import os, re, sys

POS_LOG = os.environ.get('POS_LOG', '/tmp/g2_pos.log')
SAMPLE_S = float(os.environ.get('SAMPLE_S', '2.0'))
USER_POS_RAW = os.environ.get('USER_POS', '').strip()
user_pos = None
if USER_POS_RAW:
    try:
        user_pos = tuple(float(v) for v in USER_POS_RAW.split(','))
        if len(user_pos) != 3:
            raise ValueError
    except ValueError:
        print(f"  (USER_POS 형식 오류: '{USER_POS_RAW}' — 'x,y,z' 필요)")
        user_pos = None

try:
    raw = open(POS_LOG).read()
except FileNotFoundError:
    print(f"  (위치 로그 없음: {POS_LOG})")
    sys.exit(0)

# 상태기계: timestamp_sample → x → y → z 순서로 읽어 레코드 생성.
# NED 좌표 (x=north, y=east, z=down) → ENU (x=east, y=north, z=up).
# z_valid / z_reset_counter 등 다른 z_* 필드는 "z:" 정규식과 불일치.
STATE_TS, STATE_X, STATE_Y, STATE_Z = 0, 1, 2, 3
state = STATE_TS
ts = x = y = 0.0
records = []

for line in raw.splitlines():
    if state == STATE_TS:
        m = re.match(r'\s*timestamp_sample:\s*(\d+)', line)
        if m:
            ts = int(m.group(1)) / 1e6   # us → s
            state = STATE_X
    elif state == STATE_X:
        m = re.match(r'\s*x:\s*([-+0-9.eEinf]+)', line)
        if m:
            x = float(m.group(1))
            state = STATE_Y
    elif state == STATE_Y:
        m = re.match(r'\s*y:\s*([-+0-9.eEinf]+)', line)
        if m:
            y = float(m.group(1))
            state = STATE_Z
    elif state == STATE_Z:
        m = re.match(r'\s*z:\s*([-+0-9.eEinf]+)', line)
        if m:
            z = float(m.group(1))
            # NED→ENU: x_enu=y_ned, y_enu=x_ned, z_enu=-z_ned
            records.append((ts, y, x, -z))
            state = STATE_TS

if not records:
    print(f"  (파싱 실패 — 원본 확인: head -40 {POS_LOG})")
    sys.exit(0)

t0 = records[0][0]
x0, y0, z0 = records[0][1], records[0][2], records[0][3]

# 헤더: USER_POS 있으면 dist 컬럼 추가.
if user_pos is not None:
    print(f"  {'t[s]':>6}  {'x_enu':>7}  {'y_enu':>7}  {'z_enu':>7}  {'Δx':>6}  {'Δy':>6}  {'dist':>6}")
else:
    print(f"  {'t[s]':>6}  {'x_enu':>7}  {'y_enu':>7}  {'z_enu':>7}  {'Δx':>6}  {'Δy':>6}")

# 최소 거리 추적 (USER_POS 주어졌을 때만 의미).
min_dist = float('inf')
min_t = 0.0
min_p = (0.0, 0.0, 0.0)

prev_t = -999.0
for t, xe, ye, ze in records:
    elapsed = t - t0
    if user_pos is not None:
        d = ((xe - user_pos[0]) ** 2 + (ye - user_pos[1]) ** 2 + (ze - user_pos[2]) ** 2) ** 0.5
        if d < min_dist:
            min_dist = d
            min_t = elapsed
            min_p = (xe, ye, ze)
    if elapsed - prev_t >= SAMPLE_S:
        if user_pos is not None:
            print(f"  {elapsed:6.1f}  {xe:+7.3f}  {ye:+7.3f}  {ze:+7.3f}  {xe-x0:+6.3f}  {ye-y0:+6.3f}  {d:6.3f}")
        else:
            print(f"  {elapsed:6.1f}  {xe:+7.3f}  {ye:+7.3f}  {ze:+7.3f}  {xe-x0:+6.3f}  {ye-y0:+6.3f}")
        prev_t = elapsed

# 시작·끝 요약.
xf, yf, zf = records[-1][1], records[-1][2], records[-1][3]
print(f"\n  시작 ENU: ({x0:+.3f}, {y0:+.3f}, {z0:+.3f})")
print(f"  끝   ENU: ({xf:+.3f}, {yf:+.3f}, {zf:+.3f})")
print(f"  총 변위:  (Δx={xf-x0:+.3f}, Δy={yf-y0:+.3f}, Δz={zf-z0:+.3f})")
print(f"  레코드:   {len(records)}건 ({records[-1][0]-t0:.1f}s)")

if user_pos is not None:
    r_min = 0.9  # cmsm-proof §7.1 P1 (2026-05-25 갱신): r_drone+d_brake+b_human ≈ 0.917 → 0.9
    verdict = "YES (B0 baseline 입증)" if min_dist < r_min else f"NO (r_min={r_min} 미달성)"
    print(f"\n  USER 위치 ENU: ({user_pos[0]:+.3f}, {user_pos[1]:+.3f}, {user_pos[2]:+.3f})")
    print(f"  MIN 3D 거리:   {min_dist:.3f} m @ t={min_t:.1f}s, drone=({min_p[0]:+.3f}, {min_p[1]:+.3f}, {min_p[2]:+.3f})")
    print(f"  r_min={r_min} 침입? {verdict}")
