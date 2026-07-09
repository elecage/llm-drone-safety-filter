"""bag → bag_signals → metrics end-to-end pipeline.

[B7 #12 분할 2d](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
— 6 토픽 측 message lists (rosbag2_py wrapper #6c 측 추출 결과) → 6 metric pure
function 측 계산 → `TrialMetricsReport` 잠금.

## 책임 분리 (B6c rosbag2_py wrapper ↔ 본 모듈)

| 모듈 | 입력 | 출력 | 의존성 |
|---|---|---|---|
| `eval_runner.bag_reader` (#6c ✅) | bag 디렉토리 | `BagInputs` (본 모듈) | ROS 2 / rosbag2_py |
| `eval_runner.bag_pipeline` (본 모듈) | `BagInputs` | `TrialMetricsReport` | host venv (pure) |

본 모듈 측 *pure logic* — rosbag2 측 message 추출 측 #6c (Mac mini Docker
colcon test) 측 분리. host venv 측 fixture message lists 측 pipeline 측 전체
end-to-end 검증.

## baseline-aware r_series 측 선택

paper §C 6 baseline 측 r(c) source 측 차이 ([ADR-0025 D2 + amendment 19](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d2)
+ [ADR-0005 D6](../../../docs/handover/decisions/0005-paper1-framing.md#d6)):

| baseline | r 측 source |
|---|---|
| B0 (passthrough) | 정적 r_max — CBF 적용 없음. r 부재 → V 는 worst-case r_max 회피 영역으로 측정 (가장 엄격) |
| B1a (static r_min) | 정적 r_min — c̃ 무관 (효율 baseline, tier1 `b1`) |
| B1b (static r_max) | 정적 r_max — c̃ 무관 (안전 baseline, tier1 `b1_max`) |
| B2 (modulated) | EstimatorReport 측 c̃ → $r(\\tilde c) = r_\\text{min} + (1-\\tilde c)(r_\\text{max}-r_\\text{min})$ |
| B3 (context_aug) | B2 동일 (r_series 측 동일 logic) |
| B4 (full_loop) | B2 동일 |

본 `build_r_series_for_baseline()` 측 baseline mode 측 분기 잠금. ADR-0025
amendment 19 — 종전 "B0/B1 = constant r_max" 는 B1a 가 실제 r_min 비행인데 메트릭을
r_max 로 잡던 버그였음 → B1a=r_min·B1b=r_max 로 정정.

## task_success 측 *외부 입력*

`task_success_rate(success_per_episode)` 측 *bool list* 입력 — 단일 trial 측
*scenario 별* success criterion 측 분기 (paper §C 시나리오 별 *waypoint 도달*
등). 본 모듈 측 `task_success: bool` 측 *외부 입력* (caller 측 scenario
evaluator 측 결정). 단일 trial 측 `task_success_rate` 측 0.0 (실패) 또는 1.0
(성공) 측 통과.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from eval_baselines.schemas import BaselineMode
from eval_metrics.autonomy import autonomy_response_score
from eval_metrics.bag_signals import (
    count_decisions,
    extract_loop_periods,
    extract_r_from_estimator_reports,
    gate_rejection_rate,
    positions_to_h_series,
)
from eval_metrics.latency import realtime_latency
from eval_metrics.overconservativeness import overconservativeness
from eval_metrics.query import query_rate
from eval_metrics.safety import safety_violation_rate
from eval_metrics.schemas import TimeSeries


# 6 토픽 측 timestamps 측 단조 비감소 잠금 — bag_signals helpers 측 자체 검증 책임.


@dataclass(frozen=True)
class BagInputs:
    """rosbag2 측 6 토픽 측 message lists — pipeline 측 단일 입력.

    Fields:
        drone_position_msgs: ``/vehicle_local_position`` 측 ``[(t_s, (x, y, z)), ...]``.
        setpoint_timestamps_s: ``/cmd/trajectory_setpoint_safe`` 측 timestamps [s].
            tier1 측 *실 publish* sample — realtime_latency 측 input.
        estimator_report_json_strs: ``/intent/estimator/report`` 측 ``[(t_s, json_str), ...]``.
            EstimatorReport JSON 측 c_tilde 측 r(c̃) 변환. B0/B1 측 빈 list OK.
        tier2_decision_json_strs: ``/tier2/decision`` 측 JSON strs. B0/B1/B2 측
            Tier 2 게이트 부재 → 빈 list (ARS=1.0, QR=0).
        episode_duration_s: 측정 episode 길이 [s] — wall_clock_s 측 정합 (trial_meta
            측 동일). query_rate 측 denominator + sanity.

    `clock_msgs` 측 별 필드 없음 — paper §C 측 sim time anchor 측 *bag timestamp*
    측 정합 (drone_position / setpoint / estimator 모든 timestamps 측 동일 sim
    time source).
    """

    drone_position_msgs: List[Tuple[float, Tuple[float, float, float]]]
    setpoint_timestamps_s: List[float]
    estimator_report_json_strs: List[Tuple[float, str]]
    tier2_decision_json_strs: List[str]
    episode_duration_s: float

    def __post_init__(self) -> None:
        if self.episode_duration_s <= 0.0:
            raise ValueError(
                f'episode_duration_s 양의 실수 필수 — got {self.episode_duration_s}'
            )
        # setpoint_timestamps_s 측 *최소 2 sample* 강제 (PR #139 review M-1) —
        # extract_loop_periods 측 n>=2 강제 + realtime_latency 측 의미 (worst-case
        # inter-message dt) 정합. BagInputs 측 *valid 상태* ↔ compute_trial_metrics
        # 측 *pipeline 도중* raise 측 사용자 혼란 회피.
        if len(self.setpoint_timestamps_s) < 2:
            raise ValueError(
                f'setpoint_timestamps_s 측 최소 2 sample 필요 — got '
                f'n={len(self.setpoint_timestamps_s)}. realtime_latency '
                f'(tau_loop = max inter-message dt) 측 의미 + extract_loop_periods '
                f'n>=2 강제 정합.'
            )


@dataclass(frozen=True)
class TrialMetricsReport:
    """단일 trial 측 6 metric 측 잠금 — paper §C 5×5 표 측 단일 cell input.

    [ADR-0025 D2](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d2)
    정합 — V / SR / ARS / QR / bar_r / tau_loop.

    Fields:
        safety_violation_rate (V): h(x(t)) < 0 측 fraction [0, 1]. h 측 *각 baseline
            의 선언 반경* r(c̃(t)) 기준 (B0=r_max·B1a=r_min·B1b=r_max·B2+=r(c̃)) —
            "각 baseline 이 자기 선언 안전 집합의 전방불변성을 지켰는가".
        safety_violation_rate_floor (V_floor): h_floor(x(t)) = ‖p−p_user‖ − r_min < 0
            측 fraction [0, 1]. *물리 하한 r_min* 공통 기준이라 baseline 간 *직접
            비교 가능* — "드론이 물리적 최소 안전거리 r_min 을 침범했는가" (안전 주장
            핵심). B1a 측 V 와 동일(선언 반경 = r_min).
        task_success (SR): 단일 trial 측 bool — caller 측 scenario evaluator
            결정 (paper §C 시나리오 별 success criterion 분기).
        autonomy_response_score (ARS): 1 - ask_user/total Tier 2 decisions.
            Tier 2 부재 측 1.0 (B0/B1/B2).
        query_rate (QR): ask_user / episode_duration [1/s]. Tier 2 부재 측 0.0.
        overconservativeness (bar_r): mean r [m] across trial.
        realtime_latency (tau_loop): max inter-message dt [s] for setpoint
            publishes — tier1 측 worst-case loop period.
        gate_rejection_rate (gate_R): Tier 2 게이트 reject/total ∈ [0, 1] 또는
            None (ADR-0039 D4, C3 정량). 게이트 미활성(B0–B3) 또는 결정 0 측 None
            — `metrics_aggregator._metric_applicable` 이 B4 만 표에 노출.
    """

    safety_violation_rate: float
    safety_violation_rate_floor: float
    task_success: bool
    autonomy_response_score: float
    query_rate: float
    overconservativeness: float
    realtime_latency: float
    gate_rejection_rate: Optional[float] = None

    # invariant 측 별 metric pure function 측 책임 (예: V ∈ [0, 1] safety.py 측
    # 보장). 본 dataclass 측 *aggregation 잠금* 만 책임 — `__post_init__` 측
    # 본문 없으면 정의 안 두는 게 정합 (PR #139 review C-2).


def build_r_series_for_baseline(
    baseline_mode: BaselineMode,
    estimator_report_json_strs: List[Tuple[float, str]],
    setpoint_timestamps_s: List[float],
    r_min: float,
    r_max: float,
) -> TimeSeries:
    """6 baseline 측 r(t) 시계열 잠금 — baseline mode 측 source 분기.

    | baseline | source |
    |---|---|
    | B0 | 정적 r_max — worst-case V (필터 없음, r 부재 → r_max 영역) |
    | B1a | 정적 r_min — 효율 baseline (tier1 `b1`) |
    | B1b | 정적 r_max — 안전 baseline (tier1 `b1_max`) |
    | B2 / B3 / B4 | extract_r_from_estimator_reports |

    Args:
        baseline_mode: BaselineMode enum.
        estimator_report_json_strs: ``[(t_s, json_str), ...]``. B0/B1a/B1b 측 무시
            (빈 list OK).
        setpoint_timestamps_s: B0/B1a/B1b 측 r_series timestamps anchor (h-series 측
            *nearest* 측 정합). B2+ 측 무시.
        r_min: 결정론 마진 하한 [m] ($> 0$).
        r_max: 마진 상한 [m] ($> r_\\text{min}$).

    Returns:
        TimeSeries — r(t) [m]. B0/B1b 측 *constant r_max*, B1a 측 *constant r_min*,
        B2+ 측 *r(c̃)*.

    Raises:
        ValueError: r_min/r_max invariant 위반 또는 B0/B1a/B1b 측 setpoint_timestamps
            빈 list 또는 B2+ 측 estimator_report_json_strs 빈 list.

    Note (ADR-0025 amendment 19):
        B0 측 r_max — 필터 부재로 r 정의 안 됨 → V 를 가장 엄격한 worst-case r_max
        회피 영역으로 측정. B1a 측 r_min(효율점)·B1b 측 r_max(안전점) — 각 baseline
        의 *실제 비행 반경* 으로 V·$\\bar r$ 측정.
    """
    if r_min <= 0.0 or r_min >= r_max:
        raise ValueError(
            f'r_min 양의 실수 + r_min < r_max — got r_min={r_min}, r_max={r_max}'
        )
    # 정적 r baseline (c̃ 무관) — 각 baseline 의 실제 비행 반경 (B0 만 worst-case).
    _STATIC_RADIUS = {
        BaselineMode.B0: r_max,    # worst-case V (필터 없음)
        BaselineMode.B1A: r_min,   # 효율점
        BaselineMode.B1B: r_max,   # 안전점
    }
    if baseline_mode in _STATIC_RADIUS:
        if not setpoint_timestamps_s:
            raise ValueError(
                f'baseline {baseline_mode.value} 측 setpoint_timestamps_s 빈 list 거부 — '
                f'정적 r series 측 anchor timestamps 필요.'
            )
        r_const = _STATIC_RADIUS[baseline_mode]
        return TimeSeries(
            timestamps=tuple(setpoint_timestamps_s),
            values=tuple(r_const for _ in setpoint_timestamps_s),
        )
    # B2 / B3 / B4 — estimator 측 c̃ → r(c̃)
    return extract_r_from_estimator_reports(
        estimator_report_json_strs, r_min, r_max,
    )


def compute_trial_metrics(
    inputs: BagInputs,
    baseline_mode: BaselineMode,
    user_position: Tuple[float, float, float],
    r_min: float,
    r_max: float,
    task_success: bool,
) -> TrialMetricsReport:
    """end-to-end pipeline — BagInputs + baseline + r_min/r_max + task_success → 6 metric.

    합성 순서:
      1. r_series ← build_r_series_for_baseline(baseline, estimator, setpoint, r_min, r_max)
      2. h_series ← positions_to_h_series(drone_position, user_position, r_series)
      3. V ← safety_violation_rate(h_series)
      4. (n_ask, n_total) ← count_decisions(tier2_decisions) — empty 측 (0, 0)
      5. ARS ← autonomy_response_score(n_ask, n_total) — n_total=0 측 1.0
         (eval_metrics.autonomy 측 명시 경계 잠금, "개입 0" 측 의미 정합)
      6. QR ← query_rate(n_ask, episode_duration)
      7. bar_r ← overconservativeness(r_series)
      8. periods ← extract_loop_periods(setpoint_timestamps); tau_loop ← realtime_latency(periods)

    Args:
        inputs: BagInputs.
        baseline_mode: BaselineMode — r_series source 분기 결정.
        user_position: 사용자 회피 영역 중심 (paper §C 측 *정적*, ADR-0026 D3).
        r_min: 마진 하한 [m].
        r_max: 마진 상한 [m].
        task_success: caller 측 scenario evaluator 측 결정 — 단일 trial bool.

    Returns:
        TrialMetricsReport — 6 metric 잠금.

    Raises:
        ValueError: r_min/r_max invariant 또는 BagInputs 측 위반 (bag_signals
            helpers 측 raise).
    """
    r_series = build_r_series_for_baseline(
        baseline_mode,
        inputs.estimator_report_json_strs,
        inputs.setpoint_timestamps_s,
        r_min,
        r_max,
    )
    h_series = positions_to_h_series(
        inputs.drone_position_msgs, user_position, r_series,
    )
    n_ask, n_total = count_decisions(inputs.tier2_decision_json_strs)
    # Tier 2 게이트 부재 (B0/B1/B2) 측 (n_ask, n_total) = (0, 0) →
    # autonomy_response_score(0, 0) = 1.0 잠금 (eval_metrics.autonomy 측 명시
    # 경계 잠금, "개입 0" 측 의미 정합). 본 함수 측 별 분기 없이 직접 호출 —
    # PR #139 review C-3 정정 (중복 분기 + "ZeroDivisionError 회피" 측 잘못된 이유).
    ars = autonomy_response_score(n_ask, n_total)
    qr = query_rate(n_ask, inputs.episode_duration_s)
    bar_r = overconservativeness(r_series)
    periods = extract_loop_periods(inputs.setpoint_timestamps_s)
    tau_loop = realtime_latency(periods)
    gate_rej = gate_rejection_rate(inputs.tier2_decision_json_strs)
    v = safety_violation_rate(h_series)

    # V_floor — 물리 하한 r_min 공통 기준 (모든 baseline 동일). 각 baseline 의 선언
    # 반경과 무관하게 "드론이 물리적 최소 안전거리 r_min 을 침범했는가" → cross-baseline
    # 안전 비교의 단일 기준. r 이 상수라 setpoint timestamps anchor 의 nearest 조회가
    # 항상 r_min 을 돌려줌. B1a 측 r_series 가 이미 r_min 이라 V_floor == V.
    r_floor_series = TimeSeries(
        timestamps=tuple(inputs.setpoint_timestamps_s),
        values=tuple(r_min for _ in inputs.setpoint_timestamps_s),
    )
    h_floor_series = positions_to_h_series(
        inputs.drone_position_msgs, user_position, r_floor_series,
    )
    v_floor = safety_violation_rate(h_floor_series)

    return TrialMetricsReport(
        safety_violation_rate=v,
        safety_violation_rate_floor=v_floor,
        task_success=task_success,
        autonomy_response_score=ars,
        query_rate=qr,
        overconservativeness=bar_r,
        realtime_latency=tau_loop,
        gate_rejection_rate=gate_rej,
    )
