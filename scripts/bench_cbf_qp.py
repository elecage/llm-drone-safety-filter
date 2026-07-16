#!/usr/bin/env python3
"""Tier 1 CBF-QP 풀이 시간 벤치마크 — paper §8.6 R6 (리뷰노트) 보고용.

배경: paper §7.5가 "$\\tau_\\text{loop}$은 상류 명령 스트림의 발행 주기이지
Tier 1의 연산 시간이 아니다"라고 명시하는데, 그 연산 시간 실측이 main·ESM
어디에도 없음(submission_review.md R6). 본 스크립트가 그 한 줄 수치를 생산한다.

측정 대상 = `safety/tier1/tier1_filter/cbf_qp.py`의 두 진입점:
  - cbf_qp_velocity_static     (B1 계열, 명제 1)
  - cbf_qp_velocity_modulated  (B2, 정리 2 — 논문 보고 대상)
세 분기(제약 비활성 / 제약 활성 / 활성+saturation)를 각각 측정해
worst-branch 값을 논문 수치로 쓴다. **각 케이스는 측정 전 info flag
(constraint_active·saturated)를 기대값과 대조(assert)** 해 라벨과 실제
실행 분기의 불일치를 차단한다 (self-review에서 드론 z 고도를 거리 계산에
반영하지 않아 세 케이스 전부 inactive였던 결함을 적발한 재발 방지책).

실행 (paper 보고용 수치는 **sim 호스트(Mac mini M4)에서** — 다른 실측치와
호스트 일치 의무):
    .venv/bin/python scripts/bench_cbf_qp.py
로컬 스모크 테스트(임의 호스트)도 같은 명령. 출력에 호스트 정보가 찍히므로
paper에 옮길 때 sim 호스트 출력인지 확인할 것.

파라미터는 paper Table 2 값 고정: r_min=0.9, r_max=1.8, gamma=4.0, u_max=0.5.
"""

from __future__ import annotations

import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

# 리포 루트 기준 tier1 모듈 직접 import (ROS 2 불요 — 순수 numpy 함수)
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'safety' / 'tier1'))

from tier1_filter.cbf_qp import (  # noqa: E402
    cbf_qp_velocity_modulated,
    cbf_qp_velocity_static,
)

# paper Table 2 파라미터
R_MIN, R_MAX, GAMMA, U_MAX = 0.9, 1.80, 4.0, 0.5
# 사용자 상체 높이 1.0 m — 드론을 동고도에 두어 케이스 기하가 수평 거리로 결정되게 함
P_USER = np.array([0.0, 0.0, 1.0])

N_WARMUP = 2_000
N_ITER = 100_000

# B2(modulated) 케이스: (라벨, u_nom, p_drone, r, r_dot, 기대 active, 기대 saturated)
#  - inactive: 드론이 멀리서 사용자 반대 방향 이동 → 제약 비활성, u* = u_nom
#  - active: 경계 근처(h=0.05)에서 사용자 직진(적대 setpoint 재현) → 제약 활성
#  - active+sat: 경계 위(h=0) + 반경 팽창률 최대(r_dot = u_max, feasibility 경계)
#    + 접선 성분 있는 u_nom → 보정 후 norm 초과 → saturation
CASES_MOD = [
    ('inactive',   np.array([0.3, 0.0, 0.0]),  np.array([3.0, 0.0, 1.0]),  1.2, 0.0,   False, False),
    ('active',     np.array([-0.5, 0.0, 0.0]), np.array([1.25, 0.0, 1.0]), 1.2, 0.0,   True,  False),
    ('active+sat', np.array([-0.5, 0.3, 0.0]), np.array([0.9, 0.0, 1.0]),  0.9, U_MAX, True,  True),
]

# B1(static) 케이스: (라벨, u_nom, p_drone, 기대 active, 기대 saturated) — r = R_MIN 고정
CASES_STATIC = [
    ('inactive',   np.array([0.3, 0.0, 0.0]),  np.array([3.0, 0.0, 1.0]), False, False),
    ('active',     np.array([-0.5, 0.3, 0.0]), np.array([0.9, 0.0, 1.0]), True,  False),
    ('active+sat', np.array([-0.5, 0.6, 0.0]), np.array([0.9, 0.0, 1.0]), True,  True),
]


def check_branch(label: str, info: dict, want_active: bool, want_sat: bool) -> None:
    """케이스 라벨과 실제 실행 분기의 일치를 강제."""
    got = (info['constraint_active'], info['saturated'])
    want = (want_active, want_sat)
    assert got == want, (
        f"케이스 '{label}' 분기 불일치: 기대 active={want_active}, saturated={want_sat} "
        f"/ 실제 active={got[0]}, saturated={got[1]} — 케이스 기하를 수정할 것")


def bench(fn, args: tuple, n_iter: int = N_ITER) -> list[float]:
    """fn(*args)를 n_iter회 호출, per-call 시간[µs] 리스트 반환."""
    for _ in range(N_WARMUP):
        fn(*args)
    times = []
    for _ in range(n_iter):
        t0 = time.perf_counter()
        fn(*args)
        times.append((time.perf_counter() - t0) * 1e6)
    return times


def report(label: str, times: list[float]) -> tuple[float, float, float]:
    med = statistics.median(times)
    p95 = statistics.quantiles(times, n=20)[18]
    worst = max(times)
    print(f"  {label:12s} median {med:7.2f} µs   p95 {p95:7.2f} µs   worst {worst:8.2f} µs")
    return med, p95, worst


def main() -> None:
    print(f"host: {platform.node()} / {platform.machine()} / python {platform.python_version()}")
    print(f"iterations: {N_ITER:,} per case (warmup {N_WARMUP:,})")
    print()

    print("cbf_qp_velocity_modulated (B2 — paper 보고 대상):")
    worst_med, all_p95, all_worst = 0.0, 0.0, 0.0
    for label, u_nom, p_drone, r, r_dot, want_act, want_sat in CASES_MOD:
        args = (u_nom, p_drone, P_USER, r, r_dot, GAMMA, U_MAX)
        _, info = cbf_qp_velocity_modulated(*args)
        check_branch(label, info, want_act, want_sat)
        med, p95, worst = report(label, bench(cbf_qp_velocity_modulated, args))
        worst_med = max(worst_med, med)
        all_p95 = max(all_p95, p95)
        all_worst = max(all_worst, worst)

    print()
    print("cbf_qp_velocity_static (B1 계열, 참고):")
    for label, u_nom, p_drone, want_act, want_sat in CASES_STATIC:
        args = (u_nom, p_drone, P_USER, R_MIN, GAMMA, U_MAX)
        _, info = cbf_qp_velocity_static(*args)
        check_branch(label, info, want_act, want_sat)
        report(label, bench(cbf_qp_velocity_static, args))

    period_us = 50_000.0  # 50 ms 설계 주기
    print()
    print(f"[paper 문장용 — B2 worst branch] median {worst_med:.1f} µs, "
          f"p95 {all_p95:.1f} µs, worst {all_worst:.1f} µs "
          f"= 설계 주기 50 ms의 {100 * worst_med / period_us:.4f}% (median 기준)")


if __name__ == '__main__':
    main()
