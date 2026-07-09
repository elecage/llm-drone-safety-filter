"""ADR-0025 D2 / ADR-0039 D5 — 발행 주기 $\\tau_\\text{loop}$.

정의: tier1 filter_node 가 재발행하는 setpoint 스트림의 발행 주기 (max) [s].

★ ADR-0039 D5 정정: filter_node 는 상류(nominal 발행원)의 header 를 복사해
재발행하므로 본 측정이 반영하는 것은 *상류 발행 cadence* 이지 tier1 의 연산
시간이 아니다. 또한 시뮬 절대 주기는 호스트 성능에 좌우된다. 따라서 본 metric
은 RQ3(LLM 지연 독립성)의 증거로 쓰지 않는다 — RQ3 판정 = ① 구조 논증(paper
§4: tier1 은 마지막 유효 신뢰도로 매 주기 동작, LLM 지연이 임계 경로 밖) +
② 백본별 LLM inference latency 대 tier1 주기의 자릿수 분리(TRIAL_LOG JSONL
`inference_latency_s`). 본 metric 은 파이프라인 건전성(발행 스톨 감지) *보조*
지표로만 보고 (paper §7.5).

본 metric 측 *max* (worst case) — mean 은 *typical* 측 보조.
"""

from __future__ import annotations

from typing import List


def realtime_latency(loop_periods_s: List[float]) -> float:
    """$\\tau_\\text{loop} = \\max_i \\text{loop\\_period}_i$ [s].

    Args:
        loop_periods_s: tier1 재발행 setpoint 스트림 측 period sequence
            (rosbag2 측 추출 — 상류 발행 cadence 반영, ADR-0039 D5).

    Returns:
        max period [s]. 파이프라인 건전성 보조 지표 (RQ3 증거 아님).

    Raises:
        ValueError: 빈 list 또는 음수 period.
    """
    if not loop_periods_s:
        raise ValueError(
            'realtime_latency 측 빈 list 거부 — loop period 측 1 이상 필요'
        )
    for i, p in enumerate(loop_periods_s):
        if p < 0.0:
            raise ValueError(
                f'loop_periods_s[{i}]={p} 음수 거부'
            )
    return max(loop_periods_s)
