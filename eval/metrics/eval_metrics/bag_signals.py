"""bag-agnostic helpers — rosbag2 측 message list → metric input (TimeSeries / int / list).

[ADR-0025 D4](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d4)
측 토픽 6 종 측 *message stream* 측 metric 측 input 측 변환 helpers. 본
모듈 측 **rosbag2 의존성 없음** — rosbag2_py wrapper (B6c 후속 또는 colcon
test 측 별 트랙) 측 message extraction 후 본 helpers 호출.

설계 패턴 (A3-3 정합): pure logic + ROS 2 wrapper 분리.

## 6 토픽 ↔ metric 변환 매핑 (ADR-0025 D4)

| topic | helpers | metric input |
|---|---|---|
| `/vehicle_local_position` | `positions_to_h_series` (with user_position + r_series) | safety V |
| `/cmd/trajectory_setpoint_safe` | `extract_loop_periods` | latency tau_loop |
| `/intent/grounding_confidence` | (직접 TimeSeries) | (estimator 진단) |
| `/intent/estimator/report` | `extract_r_from_estimator_reports` | overconservativeness bar_r |
| `/tier2/decision` | `count_decisions` / `gate_rejection_rate` | autonomy ARS, query QR, gate_R |
| `/clock` | (sim time normalize) | (별 모든 metric 측 sim time anchor) |

## Tier 2 decision schema (gate_node 실제 발행)

```json
{"decision": "accept"|"confirm"|"reject", "reason": <str>, "violations": [<str>], "sigma": <str>, "theta": {...}, "c": <float>}
```

`gate_node` 발행 decision ∈ {accept, confirm, reject} — 리터럴 ``ask_user`` 는
발행하지 않는다. paper §7.5 의 "확인을 요청한 횟수"($n_\\text{ask}$)에 해당하는
사건은 게이트의 **``confirm`` 결정**이다(Case 4 모순 · Case 5 중간 신뢰도 —
`tier2_gate.gate` 참조; `tier2_gate.specs` 도 "ask_user (action_class='confirm')
는 게이트 내부 응답"이라 명시해 두 개념이 애초 동일했음을 확인할 수 있다).
`count_decisions` 는 ``decision == 'confirm'`` 을 $n_\\text{ask}$ 로 집계한다
(PR #304 리뷰 발견 — 종전엔 리터럴 ``'ask_user'`` 를 세어 전 baseline
$n_\\text{ask}$ 가 구조적으로 0 이었다. ADR-0032 amendment 2026-07-03 참조).
본 모듈 helpers 는 ``decision`` 키만 요구(나머지 필드 무시). 종전 "timestamp_ns"
1차 시안 표기는 gate_node 가 발행하지 않아 정정(ADR-0039 D4 — C3 게이트
거부율 신설 정합).

**범위 한계**: *의도해석기*가 스킬 ``ask_user``(action_class=confirm, 사용자
지시 대상 명료화 질문)를 직접 호출하는 경우도 게이트를 통과하며 별도로
accept/confirm/reject 판정을 받는다(상류 이벤트). 이 경로의 accept 는 현재
$n_\\text{ask}$ 에 합산하지 않는다 — 두 신호를 섞는 것은 새 지표 설계 결정이라
본 정정의 범위 밖(ADR-0032 amendment 참조).

## r(c) 변조 (B2 baseline)

$r(\\tilde c) = r_\\text{min} + (1 - \\tilde c)(r_\\text{max} - r_\\text{min})$

`extract_r_from_estimator_reports` 측 EstimatorReport.c_tilde 측 sample 후 r(c)
변환 (B2 변조). 정적 baseline 은 c_tilde 무관 상수 — **B1a = $r_\\text{min}$**(tier1
`b1`) · **B1b = $r_\\text{max}$**(tier1 `b1_max`) · B0 = worst-case $r_\\text{max}$
(V 측정). caller 측 별 helper (ADR-0025 amendment 19 — B1 을 B1a/B1b 로 분리).
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from eval_metrics.schemas import TimeSeries, clamp_monotonic


# -------------------------------------------------------------------- safety h-series


def positions_to_h_series(
    drone_position_msgs: List[Tuple[float, Tuple[float, float, float]]],
    user_position: Tuple[float, float, float],
    r_series: TimeSeries,
) -> TimeSeries:
    """드론 위치 + 사용자 위치 + r 시계열 → h(x(t)) 시계열.

    $h(x(t)) = \\lVert p_\\text{drone}(t) - p_\\text{user} \\rVert - r(\\tilde c(t))$

    r_series 측 *drone_position* timestamps 측 nearest-neighbor lookup (단조
    비감소 timestamps 가정). 정확 sync 측 caller 책임 (rosbag2_py 측 message
    time align).

    Args:
        drone_position_msgs: ``[(timestamp_s, (x, y, z)), ...]`` — bag_reader
            측 ``/vehicle_local_position`` 측 추출. timestamps 단조 비감소
            강제.
        user_position: 사용자 회피 영역 중심 (paper §C 측 *정적* 가정, ADR-0026 D3).
        r_series: TimeSeries — $r(\\tilde c(t))$ 측 시계열 [m].

    Returns:
        h(x(t)) TimeSeries — drone_position 측 timestamps + 동일 길이 h values.

    Raises:
        ValueError: drone_position_msgs 측 빈 list 또는 timestamps 단조 비감소
            위반 또는 r_series 측 빈.
    """
    if not drone_position_msgs:
        raise ValueError('drone_position_msgs 빈 list 거부')
    if not r_series.timestamps:
        raise ValueError('r_series 빈 TimeSeries 거부')

    # drone timestamps 단조화 — 레코더 jitter 역전 clamp (clamp_monotonic 정책 정합).
    _ct = clamp_monotonic([t for t, _ in drone_position_msgs])
    drone_position_msgs = [
        (ct, xyz) for ct, (_, xyz) in zip(_ct, drone_position_msgs)
    ]

    ux, uy, uz = user_position
    timestamps: List[float] = []
    h_values: List[float] = []
    for t, (px, py, pz) in drone_position_msgs:
        dist = math.sqrt((px - ux) ** 2 + (py - uy) ** 2 + (pz - uz) ** 2)
        r = _nearest_value(r_series, t)
        timestamps.append(t)
        h_values.append(dist - r)

    return TimeSeries(timestamps=tuple(timestamps), values=tuple(h_values))


def _nearest_value(series: TimeSeries, query_t: float) -> float:
    """series.timestamps 측 query_t 측 nearest-neighbor 측 value 반환.

    series.timestamps 단조 비감소 가정 (TimeSeries invariant 보장).

    *PR #110 review E-2 명시*: 측 단순 *linear scan* O(N) — paper §C trial 측
    sample 수 ~ 1000 + 격자 1000 trial × 6 metric → ~ 10^9 ops 측 충분
    (Python ~ 10 s). bisect-based O(log N) 측 100× 빠르나 본 PR 측 충분, 후속
    backlog (ROADMAP C30) 측 정정 가능.

    *PR #110 review E-7 명시*: 측 *nearest-neighbor* — query_t 측 *미래
    sample* 측도 nearest 가능. *bag post-hoc* 측 metric 계산 측 OK (양쪽
    sample 모두 trial 종료 후 알려진). *real-time wiring* 측 *causal* (query_t
    이전 측 마지막 sample) 측 별 helper 필요 (B7 후속 측 ``_latest_value_at_or_before``).
    """
    n = len(series.timestamps)
    if n == 0:
        raise ValueError('빈 series 측 nearest 불가')
    # 단순 linear scan — paper §C trial 측 sample 수 1000s 측 sufficient
    best_i = 0
    best_dt = abs(series.timestamps[0] - query_t)
    for i in range(1, n):
        dt = abs(series.timestamps[i] - query_t)
        if dt < best_dt:
            best_i = i
            best_dt = dt
    return series.values[best_i]


# -------------------------------------------------------------------- r series (B2)


def extract_r_from_estimator_reports(
    report_json_strs: List[Tuple[float, str]],
    r_min: float,
    r_max: float,
) -> TimeSeries:
    """EstimatorReport JSON 측 c_tilde sample → $r(\\tilde c)$ 시계열.

    $r(\\tilde c) = r_\\text{min} + (1 - \\tilde c)(r_\\text{max} - r_\\text{min})$
    — B2 modulated baseline. 정적 baseline (B1a=$r_\\text{min}$·B1b=$r_\\text{max}$)
    측 별 helper (bag_pipeline.build_r_series_for_baseline, constant r).

    *PR #110 review E-11 명시*: 측 EstimatorReport 측 *c_tilde* (rate-limited
    입력) 사용 — ADR-0020 D4 측 *c_raw* (estimator $g$ 원시 출력) vs *c_tilde*
    (변화율 제한기 통과 후 CBF 측 입력) 측 *두 신호 분리*. 본 metric 측
    *안전 계층 측 실 사용* (c_tilde) 측 정합. c_raw 측 *진단 채널* (paper §C
    부록 보고 측 별 분석) 측 사용.

    Args:
        report_json_strs: ``[(timestamp_s, EstimatorReport JSON str), ...]``.
            EstimatorReport.c_tilde 측 추출 후 r(c) 변환.
        r_min: 결정론 마진 하한 [m] ($> 0$).
        r_max: 마진 상한 [m] ($> r_\\text{min}$).

    Returns:
        TimeSeries — r(c_tilde(t)) [m].

    Raises:
        ValueError: r_min/r_max invariant 위반 ($r_\\text{min} \\leq 0$ 또는
            $r_\\text{min} \\geq r_\\text{max}$) 또는 빈 list 또는 c_tilde 측
            $[0, 1]$ 밖.
        KeyError / json.JSONDecodeError: EstimatorReport JSON 측 c_tilde 키
            부재 또는 parse 실패.
    """
    if not report_json_strs:
        raise ValueError('report_json_strs 빈 list 거부')
    if r_min <= 0.0:
        raise ValueError(f'r_min 양의 실수 — got {r_min}')
    if r_min >= r_max:
        raise ValueError(f'r_min < r_max 필요 — got r_min={r_min}, r_max={r_max}')

    delta = r_max - r_min
    timestamps: List[float] = []
    r_values: List[float] = []
    for t, payload in report_json_strs:
        report = json.loads(payload)
        c_tilde = float(report['c_tilde'])
        if not (0.0 <= c_tilde <= 1.0):
            raise ValueError(
                f'c_tilde 측 $[0, 1]$ — got {c_tilde} (timestamp {t})'
            )
        r = r_min + (1.0 - c_tilde) * delta
        timestamps.append(t)
        r_values.append(r)

    return TimeSeries(timestamps=tuple(timestamps), values=tuple(r_values))


# -------------------------------------------------------------------- Tier 2 decisions


_ALLOWED_DECISIONS = frozenset({'accept', 'reject', 'confirm'})


def count_decisions(decision_json_strs: List[str]) -> Tuple[int, int]:
    """Tier 2 decision list 측 ``(n_ask, n_total)`` 통합 count.

    PR #110 review E-1 정정 — 직전 두 함수 (``count_ask_user_decisions`` +
    ``count_total_commands``) 분리 측 ``count_total`` 측 *raw len()* 측 schema
    검증 누락 → silent invalid decision count 위험. 본 통합 함수 측 schema
    검증 + 두 count 한 번 측 atomic 반환.

    ARS 분모 명세 ([ADR-0025 D2 amendment 2](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d2))
    = *Tier 2 처분 σ 전체*. 두 count 측 caller 측
    ``autonomy_response_score(n_ask, n_total)`` / ``query_rate(n_ask, T)``
    측 input.

    **PR #304 리뷰 정정 (2026-07-03, ADR-0032 amendment)**: 종전엔 리터럴
    ``decision == 'ask_user'`` 측 count 했으나 `gate_node`(``tier2_gate.gate``)
    가 발행하는 decision ∈ {accept, confirm, reject} 뿐이라(ask_user 미발행)
    $n_\\text{ask}$ 가 전 baseline·전 trial 구조적으로 0 이었다(ARS≡1.0,
    QR≡0). paper §7.5 "확인을 요청한 횟수" 정의 + `tier2_gate.specs`
    ("ask_user (action_class='confirm')는 게이트 내부 응답")에 따라 $n_\\text{ask}$
    는 **``decision == 'confirm'``** 카운트로 정정한다.

    Args:
        decision_json_strs: ``[Tier 2 decision JSON str, ...]``. 각 JSON 측
            gate_node 발행 ``{"decision", "reason", "violations", "sigma",
            "theta", "c"}`` (decision ∈ {accept, confirm, reject}). 본 helper 는
            ``decision`` 키만 요구.

    Returns:
        Tuple ``(n_ask, n_total)``:
          - n_ask: ``decision == 'confirm'`` 측 count (사용자에게 확인을
            요청한 횟수).
          - n_total: ``len(decision_json_strs)`` (= Tier 2 처분 σ 전체).

    Raises:
        KeyError / json.JSONDecodeError: JSON 측 ``decision`` 키 부재 또는
            parse 실패.
        ValueError: decision value 측 _ALLOWED_DECISIONS 외.
    """
    n_ask = 0
    for payload in decision_json_strs:
        d = json.loads(payload)
        decision = d['decision']
        if decision not in _ALLOWED_DECISIONS:
            raise ValueError(
                f'unknown Tier 2 decision: {decision!r} '
                f'(허용 = {sorted(_ALLOWED_DECISIONS)!r})'
            )
        if decision == 'confirm':
            n_ask += 1
    return n_ask, len(decision_json_strs)


def gate_rejection_rate(decision_json_strs: List[str]) -> Optional[float]:
    """Tier 2 게이트 거부율 — ``n_reject / n_total`` (ADR-0039 D4, C3 정량 지표).

    게이트가 위험·부정합 명령을 거부(reject)한 비율 = 계획 수준 검증 게이트(C3)의
    직접 정량. ARS/QR(`count_decisions`, 2026-07-03 정정 후)은 게이트의 ``confirm``
    결정(사용자 확인 요청) 빈도를 재는 RQ2 자율성 축 지표라 C3(명령 거부)와는
    다른 차원 — reject 비율은 이 함수가 별도로 C3 headline 으로 담당한다.

    게이트 미활성(B0–B3, tier2_decision 빈 list) 또는 B4 라도 게이트가 한 번도
    결정하지 않은 trial 측 ``None`` (N/A) — `metrics_aggregator._metric_applicable`
    이 B4 만 표에 노출하고, 집계는 None trial 을 제외한다.

    Args:
        decision_json_strs: ``/tier2/decision`` JSON strs (각 ``{"decision", ...}``).

    Returns:
        ``n_reject / n_total`` ∈ [0, 1], 또는 n_total==0 측 None.

    Raises:
        ValueError: decision value 측 _ALLOWED_DECISIONS 외.
        KeyError / json.JSONDecodeError: ``decision`` 키 부재 또는 parse 실패.
    """
    if not decision_json_strs:
        return None
    n_reject = 0
    for payload in decision_json_strs:
        d = json.loads(payload)
        decision = d['decision']
        if decision not in _ALLOWED_DECISIONS:
            raise ValueError(
                f'unknown Tier 2 decision: {decision!r} '
                f'(허용 = {sorted(_ALLOWED_DECISIONS)!r})'
            )
        if decision == 'reject':
            n_reject += 1
    return n_reject / len(decision_json_strs)


# -------------------------------------------------------------------- loop periods


def extract_loop_periods(
    setpoint_timestamps_s: List[float],
) -> List[float]:
    """tier1 setpoint timestamps → loop period sequence [s].

    inter-message $\\text{dt}_i = t_{i+1} - t_i$. ADR-0025 D2 측 $\\tau_\\text{loop}
    = \\max_i \\text{dt}_i$ — `realtime_latency` 측 input.

    Args:
        setpoint_timestamps_s: ``/cmd/trajectory_setpoint_safe`` 측 timestamps.
            레코더 jitter 역전은 ``clamp_monotonic`` 으로 단조화(해당 period 0).

    Returns:
        ``[t_1 - t_0, t_2 - t_1, ...]`` — n-1 periods (모두 ≥ 0). τ_loop = max.

    Raises:
        ValueError: < 2 sample.

    Note (ADR-0039 D5): τ_loop 은 *상류 발행 cadence* 측정이라 bag 기록 시각엔
    레코더·전송 jitter 가 섞여 인접 sample 이 sub-ms 로 역전될 수 있다. 입력을
    단조화(clamp)해 흡수한다 — 종전엔 음수 dt 에 raise 해 *단일 trial 의 역전이
    전체 집계를 크래시*시켰다(2026-07-01 적발). clamp 된 쌍은 period 0(τ_loop=max 무관).
    """
    n = len(setpoint_timestamps_s)
    if n < 2:
        raise ValueError(
            f'extract_loop_periods 측 sample $\\geq 2$ 필요 — got n={n}'
        )
    ts = clamp_monotonic(setpoint_timestamps_s)
    return [ts[i] - ts[i - 1] for i in range(1, n)]
