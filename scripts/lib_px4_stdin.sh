#!/usr/bin/env bash
# lib_px4_stdin.sh — PX4 헤드리스 SITL 의 stdin 블로킹 헬퍼 (세션 53).
#
# 문제: HEADLESS=1 로 PX4 SITL 을 nohup 백그라운드 기동하면 stdin 이 EOF
# (/dev/null·닫힌 fd)가 된다. PX4 의 인터랙티브 `pxh>` 콘솔은 EOF 를 받으면
# 프롬프트를 *고속 재출력*하는 루프에 빠져 로그를 ~3 MB/s (≈ 70 MB / 30 s) 로
# 폭증시킨다 → 장기 격자(P5·Track B)에서 로그가 수십 GB → 디스크 100% → Docker
# 정지 (세션 49·51·52 디스크 위기 3회 반복; 진단 = sitl-log-disk-balloon 메모리).
#
# 해결: stdin 을 *데이터 없이 열린 채 유지되는* FIFO 로 준다. 콘솔의 read() 가
# EOF 대신 블록 → 프롬프트 재출력 없음 (세션 53 실측: 30 s 에 69 MB → 7.7 KB,
# ≈ 9000× 감소; PX4 부팅·ARM·Takeoff 정상). holder(sleep)가 FIFO 를 쓰기로 열어
# 두기만 하고 아무것도 안 쓴다.
#
# 사용 (★ command substitution 금지 — 아래 주의 참조):
#   source "$REPO_ROOT/scripts/lib_px4_stdin.sh"
#   px4_stdin_fifo                       # FIFO 준비 + holder 기동 → PX4_STDIN_FIFO_READY 설정
#   ... nohup "$T1_SCRIPT" > "$LOG" 2>&1 < "$PX4_STDIN_FIFO_READY" &
#   px4_stdin_cleanup                    # down 시 holder 종료 + FIFO 제거
#
# 이식성 주의 (세션 53 실측):
#   - macOS 에 `setsid` *부재* → holder detach 는 `nohup ... &` (PX4 자체와 동일
#     패턴, 기동 스크립트 종료 후 생존).
#   - `exec -a` 의존 회피 → holder 식별은 bash -c 문자열의 주석 태그
#     (`# px4-stdin-holder`)로, pkill -f 가 그 bash 프로세스를 잡는다.
#   - px4_stdin_fifo 를 `$(...)` 안에서 호출하면 holder 가 command-substitution
#     서브셸의 백그라운드 작업이 되어 조기 소멸 → PX4 의 FIFO read-open 이 영영
#     블록(0-byte 로그·미기동). 반드시 *현재 셸에서 statement 로* 호출하고 경로는
#     전역 변수 PX4_STDIN_FIFO_READY 로 받는다.

PX4_STDIN_FIFO_PATH="${PX4_STDIN_FIFO_PATH:-/tmp/px4_sitl_stdin.fifo}"
PX4_STDIN_HOLDER_TAG="px4-stdin-holder"
PX4_STDIN_FIFO_READY=""

# 이전 holder 종료 + FIFO 재생성 + 새 holder(nohup detached) 기동. 결과 경로를
# 전역 PX4_STDIN_FIFO_READY 에 설정. mkfifo 실패 시 /dev/null 폴백(스팸 위험은
# 있으나 기동은 진행 — 안전 측면 무해).
px4_stdin_fifo() {
  pkill -f "$PX4_STDIN_HOLDER_TAG" 2>/dev/null || true
  rm -f "$PX4_STDIN_FIFO_PATH" 2>/dev/null || true
  if ! mkfifo "$PX4_STDIN_FIFO_PATH" 2>/dev/null; then
    PX4_STDIN_FIFO_READY="/dev/null"
    return 1
  fi
  # holder = FIFO 를 쓰기로 연 채 영원히 블록(아무것도 안 씀). bash 가 redirect 로
  # FIFO 를 잡고 sleep 자식을 기다린다 → pkill 로 bash 종료 시 FIFO fd 해제.
  # `> fifo` 는 reader(PX4)가 열 때까지 블록하므로 반드시 백그라운드(&)로.
  nohup bash -c "sleep 2147483647  # $PX4_STDIN_HOLDER_TAG" \
    > "$PX4_STDIN_FIFO_PATH" 2>/dev/null &
  PX4_STDIN_FIFO_READY="$PX4_STDIN_FIFO_PATH"
  return 0
}

# down.sh 정리용 — holder 종료 + FIFO 제거.
px4_stdin_cleanup() {
  pkill -f "$PX4_STDIN_HOLDER_TAG" 2>/dev/null || true
  rm -f "$PX4_STDIN_FIFO_PATH" 2>/dev/null || true
}
