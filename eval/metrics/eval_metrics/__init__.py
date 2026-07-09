"""eval_metrics — paper §C 정량 metric 6 종 (ADR-0025 D2 amendment 2026-05-26).

pure-Python library (host venv, ROS 2 무관) — bag_reader (B6b 후속) 가 rosbag2
측 trial bag 측 시계열/event 추출 후 본 모듈 측 metric function 호출.

6 metric (ADR-0025 D2):
  safety.py            — V (안전 위반율, sec ratio)
  success.py           — SR (작업 성공률)
  autonomy.py          — ARS (사용자 자율감 proxy)
  query.py             — QR (명료화 질문 빈도, 1/s)
  overconservativeness.py  — \\bar r (회피 영역 반경 시간 평균, m)
  latency.py           — \\tau_loop (tier1 CBF-QP loop period max, s)

ADR-0025 D5 측 1차 시안 "1 큰 PR (5 모듈 묶음)" 표기는 amendment 2 의 6번째
metric (\\bar r) 추가 전 — 본 패키지 측 *6 모듈*. PR #106 머지 후 B6a (본 PR
6 metric) + B6b (bag_reader) 분할.
"""
