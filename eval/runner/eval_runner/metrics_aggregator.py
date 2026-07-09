"""격자(1,200 trial) 순회·집계 파이프라인 — paper §8 비교 표 산출 (ADR-0032).

per-trial 메트릭 6종(`compute_trial_metrics`)은 이미 구현되어 있으므로 본 모듈의
신규분은 **(a) 격자 순회·집계·통계** 와 **(b) `task_success`(SR) post-hoc 판정의
연결** 이다. SR 기하 판정 자체는 `task_success_geom` (순수)에 격리.

## 설계 — 순수 집계 ↔ I/O 분리 (ADR-0032 D4)

| 함수 | 책임 | 의존성 |
|---|---|---|
| `aggregate_records` | TrialRecord 리스트 → 그룹 통계 (순수) | host venv |
| `aggregate_run` | output_root 순회 + bag 읽기 + SR 판정 → AggregateReport | ROS 2 (read_bag) |

`aggregate_records` 는 fixture TrialRecord 로 host venv 단위 테스트 가능. 실 bag
e2e(`read_bag`)는 rosbag2 환경(Docker colcon / 맥미니, ADR-0032 미해결 3).

## 그룹화·통계 (ADR-0032 D5)

- 그룹: **(시나리오 × baseline × track)** 셀 + **baseline × track pooled**.
- track: A = 정상(fault_class=none) · B = 결함 주입. SR 은 Track A 만(D3) — Track
  B 셀 SR = N/A.
- 통계: 셀별 평균 ± 95% 신뢰구간(정규 근사, n≥2). n·제외 수 함께 보고.

## 메트릭 적용성 (ADR-0032 메트릭 의미론 표)

| 메트릭 | N/A 조건 |
|---|---|
| V 안전위반율 | 없음 (전 baseline·track) |
| SR 작업 성공률 | Track B (D3) |
| ARS 자율 응답 점수 | 없음 (티어 2 부재 = 1.0) |
| QR 질의율 | 없음 (티어 2 부재 = 0) |
| $\\bar r$ 과보수성 | B0 (r 부재 — 필터 없음) |
| $\\tau_\\text{loop}$ 지연 | 없음 (baseline 무관) |

## 무결성 게이트 (ADR-0032 D6 — 조용한 제외 금지)

집계 진입 전 `scan_trial_bag_statuses` 호출 — `complete` trial 만 집계, incomplete/
unknown/fault_not_applicable 은 개수 + trial id 로 명시 보고.
`fault_not_applicable` (제3 범주, ADR-0037 amend)은 의도 계층의 명료화 후퇴로
주입이 정의되지 않은 trial — 지표 풀·결함 통계에서 제외하되 md/json 출력에
별도 카운트 + trial id 목록을 명시한다 (조용한 제외 금지).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from eval_baselines.schemas import BaselineMode
from eval_metrics.schemas import TrialMetadata
from eval_metrics.trial_meta import load_trial_metadata
from eval_runner.bag_integrity import (
    BagStatusScan,
    find_rosbag_metadata,
    format_bag_status_scan,
    scan_trial_bag_statuses,
)
from eval_runner.bag_pipeline import (
    BagInputs,
    TrialMetricsReport,
    compute_trial_metrics,
)
from eval_runner.bag_reader import read_bag
from eval_runner.task_success_geom import (
    DEFAULT_ALTITUDE_M,
    DEFAULT_DELTA_M,
    DEFAULT_STANDOFF_M,
    trial_task_success,
)


# 메트릭 키 — TrialMetricsReport 필드명 정합 (출력 순서 = paper §8 표 열 순서).
METRIC_KEYS: Tuple[str, ...] = (
    'safety_violation_rate',
    'safety_violation_rate_floor',
    'task_success',
    'autonomy_response_score',
    'query_rate',
    'overconservativeness',
    'realtime_latency',
    'gate_rejection_rate',
)
# paper §8 표 헤더 라벨 (메트릭 기호).
METRIC_LABELS: Dict[str, str] = {
    'safety_violation_rate': 'V',
    'safety_violation_rate_floor': 'V_floor',
    'task_success': 'SR',
    'autonomy_response_score': 'ARS',
    'query_rate': 'QR',
    'overconservativeness': 'bar_r',
    'realtime_latency': 'tau_loop',
    'gate_rejection_rate': 'gate_R',
}

TRACK_NORMAL = 'A'
TRACK_FAULT = 'B'

# 95% 신뢰구간 정규 근사 z-값.
_Z95 = 1.959963984540054


def track_of(meta: TrialMetadata) -> str:
    """trial → track 분류 — 'A'(정상) | 'B'(결함). fault_class=none = Track A."""
    return TRACK_NORMAL if meta.fault_class == 'none' else TRACK_FAULT


def _metric_applicable(metric: str, baseline: str, track: str) -> bool:
    """메트릭이 (baseline, track) 그룹에 적용 가능한가 (N/A 정책, ADR-0032).

    - SR: Track A 만 (D3 — 결함 trial 은 작업이 의도적으로 손상되어 무의미).
    - bar_r(overconservativeness): B0 제외 (필터 없음 → r 부재, 의미론 표).
    - gate_R(gate_rejection_rate): B4 만 (tier2 게이트 활성, ADR-0039 D4 — B0–B3
      는 게이트 부재로 decision 0).
    그 외(V·ARS·QR·tau_loop)는 전 baseline·track 적용.
    """
    if metric == 'task_success':
        return track == TRACK_NORMAL
    if metric == 'overconservativeness':
        return baseline != 'B0'
    if metric == 'gate_rejection_rate':
        return baseline == 'B4'
    return True


@dataclass(frozen=True)
class TrialRecord:
    """단일 trial 의 메타데이터 + 6 메트릭 — 집계 입력 단위.

    Fields:
        trial_id: trial 디렉토리 이름.
        meta: TrialMetadata (scenario·baseline·fault_class·track 판정 근거).
        metrics: TrialMetricsReport (6 메트릭). task_success 는 Track A 만 의미
            있음 (Track B 는 집계에서 SR 제외).
    """

    trial_id: str
    meta: TrialMetadata
    metrics: TrialMetricsReport


@dataclass(frozen=True)
class MetricStat:
    """단일 메트릭의 그룹 통계 — 평균 ± 95% CI.

    Fields:
        mean: 표본 평균 (n=0 또는 N/A 시 None).
        ci95_half: 95% 신뢰구간 반폭 (정규 근사, n<2 시 None).
        n: 집계에 포함된 trial 수.
        applicable: 메트릭이 그룹에 적용 가능한가 (False = 표에서 N/A 표기).
    """

    mean: Optional[float]
    ci95_half: Optional[float]
    n: int
    applicable: bool


@dataclass(frozen=True)
class GroupAggregate:
    """(scenario × baseline × track) 셀 또는 (baseline × track) pooled 그룹 통계.

    Fields:
        scenario: 'S5'/'S6' (pooled 그룹은 None).
        baseline: 'B0'–'B4'.
        track: 'A' | 'B'.
        n_trials: 그룹 trial 수.
        stats: 메트릭 키 → MetricStat.
    """

    scenario: Optional[str]
    baseline: str
    track: str
    n_trials: int
    stats: Dict[str, MetricStat]


@dataclass(frozen=True)
class AggregateReport:
    """집계 산출물 전체 — paper §8 표 + 기계 판독 원장.

    Fields:
        cells: (scenario × baseline × track) 셀 통계 (정렬).
        pooled: (baseline × track) pooled 통계 (시나리오 합산, 정렬).
        scan: bag_status 스캔 (complete/incomplete/unknown/fault_not_applicable
            — 제외 명시 보고).
        n_aggregated: 집계에 실제 포함된 complete trial 수.
    """

    cells: Tuple[GroupAggregate, ...]
    pooled: Tuple[GroupAggregate, ...]
    scan: BagStatusScan
    n_aggregated: int


# ----------------------------------------------------------------- 순수 집계


def _mean_ci(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """표본 평균 + 95% CI 반폭(정규 근사). n<1 → (None, None), n=1 → (mean, None)."""
    n = len(values)
    if n == 0:
        return None, None
    mean = sum(values) / n
    if n < 2:
        return mean, None
    var = sum((v - mean) ** 2 for v in values) / (n - 1)  # 표본 분산 (ddof=1)
    std_err = math.sqrt(var) / math.sqrt(n)
    return mean, _Z95 * std_err


def _aggregate_group(
    records: Sequence[TrialRecord],
    scenario: Optional[str],
    baseline: str,
    track: str,
) -> GroupAggregate:
    """동일 (baseline, track) [+ scenario] 그룹 records → GroupAggregate."""
    stats: Dict[str, MetricStat] = {}
    for metric in METRIC_KEYS:
        applicable = _metric_applicable(metric, baseline, track)
        if not applicable:
            stats[metric] = MetricStat(
                mean=None, ci95_half=None, n=0, applicable=False,
            )
            continue
        # task_success 는 bool → 0/1 비율 (SR). 그 외는 float 시계열 요약.
        # gate_R 은 게이트 미결정 trial 측 None → 제외 (B4 그룹 내에서도 결정 0
        # 인 trial 은 N/A, ADR-0039 D4). 기존 metric 은 항상 float 라 무영향.
        values = [
            float(getattr(r.metrics, metric))
            for r in records
            if getattr(r.metrics, metric) is not None
        ]
        mean, ci = _mean_ci(values)
        stats[metric] = MetricStat(
            mean=mean, ci95_half=ci, n=len(values), applicable=True,
        )
    return GroupAggregate(
        scenario=scenario,
        baseline=baseline,
        track=track,
        n_trials=len(records),
        stats=stats,
    )


def aggregate_records(records: Sequence[TrialRecord]) -> AggregateReport:
    """TrialRecord 리스트 → (scenario × baseline × track) 셀 + pooled 통계 (순수).

    무결성 게이트(bag_status)는 caller(`aggregate_run`)가 적용 — 본 함수는 전달된
    records 전부를 집계한다 (테스트 격리). scan 은 caller 가 주입하거나 빈 scan.

    Args:
        records: 집계 대상 trial (complete 만 — caller 책임).

    Returns:
        AggregateReport — scan 은 빈 BagStatusScan(caller 가 채움), n_aggregated =
        len(records).
    """
    cells: Dict[Tuple[str, str, str], List[TrialRecord]] = {}
    pooled: Dict[Tuple[str, str], List[TrialRecord]] = {}
    for r in records:
        track = track_of(r.meta)
        cells.setdefault((r.meta.scenario, r.meta.baseline, track), []).append(r)
        pooled.setdefault((r.meta.baseline, track), []).append(r)

    cell_aggs = tuple(
        _aggregate_group(grp, scenario, baseline, track)
        for (scenario, baseline, track), grp in sorted(cells.items())
    )
    pooled_aggs = tuple(
        _aggregate_group(grp, None, baseline, track)
        for (baseline, track), grp in sorted(pooled.items())
    )
    return AggregateReport(
        cells=cell_aggs,
        pooled=pooled_aggs,
        scan=BagStatusScan(
            complete_ids=(), incomplete_ids=(), unknown_ids=(),
            fault_not_applicable_ids=(),
        ),
        n_aggregated=len(records),
    )


# ----------------------------------------------------------------- I/O 드라이버


def _resolve_trial_params(
    meta: TrialMetadata,
) -> Tuple[BaselineMode, Tuple[float, float, float], float, float]:
    """trial_meta → (baseline_mode, user_position_local, r_min, r_max) (ADR-0032 D7).

    scenario_params.tier1_cbf_params 단일 소스에서 resolve. compute_trial_metrics
    의 r-series 분기(B1a→r_min·B1b→r_max)는 baseline_mode 가 결정.
    """
    # 지연 import — scenario_params 는 PYTHONPATH(conftest / launch) 의존.
    from scenario_params.params import tier1_cbf_params

    params = tier1_cbf_params(meta.scenario)
    user_position = (
        params['user_local_x'],
        params['user_local_y'],
        params['user_local_z'],
    )
    baseline_mode = BaselineMode[meta.baseline]  # 'B2' → BaselineMode.B2 (이름)
    return baseline_mode, user_position, params['r_min'], params['r_max']


def compute_record(
    trial_id: str,
    meta: TrialMetadata,
    inputs: BagInputs,
    *,
    standoff_m: float = DEFAULT_STANDOFF_M,
    altitude_m: float = DEFAULT_ALTITUDE_M,
    delta_m: float = DEFAULT_DELTA_M,
) -> TrialRecord:
    """단일 trial 의 BagInputs → TrialRecord (SR post-hoc 판정 + 6 메트릭).

    SR 은 Track A 만 기하 판정(ADR-0032 D2·D3) — Track B 는 task_success=False 로
    두되(집계에서 SR 제외) V·bar_r 로 평가. read_bag 분리로 host 단위 테스트 가능.

    Args:
        trial_id: trial 식별자.
        meta: TrialMetadata.
        inputs: BagInputs (read_bag 출력).
        standoff_m / altitude_m / delta_m: vantage 기하·도달 허용오차.

    Returns:
        TrialRecord.
    """
    baseline_mode, user_position, r_min, r_max = _resolve_trial_params(meta)
    if track_of(meta) == TRACK_NORMAL:
        task_success = trial_task_success(
            meta.scenario,
            inputs.drone_position_msgs,
            standoff_m=standoff_m,
            altitude_m=altitude_m,
            delta_m=delta_m,
        )
    else:
        # Track B — SR 무의미(D3). 집계에서 제외되나 compute_trial_metrics 가
        # bool 을 요구하므로 False 전달.
        task_success = False
    metrics = compute_trial_metrics(
        inputs,
        baseline_mode,
        user_position,
        r_min,
        r_max,
        task_success,
    )
    return TrialRecord(trial_id=trial_id, meta=meta, metrics=metrics)


def aggregate_run(
    output_root: Union[str, Path],
    backbone: str,
    *,
    standoff_m: float = DEFAULT_STANDOFF_M,
    altitude_m: float = DEFAULT_ALTITUDE_M,
    delta_m: float = DEFAULT_DELTA_M,
    read_bag_fn: Callable[..., BagInputs] = read_bag,
) -> AggregateReport:
    """``<output_root>/<backbone>/`` 격자 순회 → AggregateReport (I/O 드라이버).

    절차: (1) `scan_trial_bag_statuses` (무결성 게이트, D6) → (2) complete trial
    만 trial_meta 로드 + bag 읽기 + SR 판정 + 6 메트릭 → (3) `aggregate_records`
    그룹 통계 → (4) scan 주입.

    `read_bag_fn` 주입으로 테스트는 가짜 reader 를 전달할 수 있다 (실 rosbag2
    없이 드라이버 경로 검증). 기본값 = `read_bag` (rosbag2 필요).

    Args:
        output_root: RunConfig.output_root.
        backbone: run-level backbone 식별자 (trial bag 경로 정합).
        standoff_m / altitude_m / delta_m: SR vantage 기하·도달 허용오차.
        read_bag_fn: bag_dir + episode_duration_s → BagInputs.

    Returns:
        AggregateReport — scan 에 complete/incomplete/unknown 명시.
    """
    scan = scan_trial_bag_statuses(output_root, backbone)
    root = Path(output_root) / backbone

    records: List[TrialRecord] = []
    for trial_id in scan.complete_ids:
        trial_dir = root / trial_id
        meta = load_trial_metadata(trial_dir / 'trial_meta.yaml')
        meta_path = find_rosbag_metadata(trial_dir)
        if meta_path is None:
            # scan 은 trial_meta.bag_status='complete' 를 신뢰하나 rosbag2
            # metadata 가 사라졌으면 읽기 불가 — incomplete 로 강등 보고.
            scan = _demote_to_incomplete(scan, trial_id)
            continue
        inputs = read_bag_fn(
            meta_path.parent, episode_duration_s=meta.wall_clock_s,
        )
        records.append(
            compute_record(
                trial_id, meta, inputs,
                standoff_m=standoff_m,
                altitude_m=altitude_m,
                delta_m=delta_m,
            )
        )

    agg = aggregate_records(records)
    return AggregateReport(
        cells=agg.cells,
        pooled=agg.pooled,
        scan=scan,
        n_aggregated=len(records),
    )


def _demote_to_incomplete(scan: BagStatusScan, trial_id: str) -> BagStatusScan:
    """complete 였으나 rosbag2 metadata 부재로 읽기 불가한 trial 을 incomplete 로 강등."""
    return BagStatusScan(
        complete_ids=tuple(t for t in scan.complete_ids if t != trial_id),
        incomplete_ids=tuple(sorted((*scan.incomplete_ids, trial_id))),
        unknown_ids=scan.unknown_ids,
        fault_not_applicable_ids=scan.fault_not_applicable_ids,
    )


# ----------------------------------------------------------------- 출력


def _stat_to_dict(stat: MetricStat) -> Dict[str, object]:
    return {
        'mean': stat.mean,
        'ci95_half': stat.ci95_half,
        'n': stat.n,
        'applicable': stat.applicable,
    }


def _group_to_dict(g: GroupAggregate) -> Dict[str, object]:
    return {
        'scenario': g.scenario,
        'baseline': g.baseline,
        'track': g.track,
        'n_trials': g.n_trials,
        'metrics': {m: _stat_to_dict(g.stats[m]) for m in METRIC_KEYS},
    }


def report_to_json_dict(report: AggregateReport) -> Dict[str, object]:
    """AggregateReport → 기계 판독 JSON dict (원장, ADR-0032 D5)."""
    return {
        'n_aggregated': report.n_aggregated,
        'bag_status': {
            'complete': len(report.scan.complete_ids),
            'incomplete': list(report.scan.incomplete_ids),
            'unknown': list(report.scan.unknown_ids),
            # 제3 범주 (ADR-0037 amend) — 명료화 후퇴로 주입 미정의. 지표 풀
            # 제외 + trial id 명시 (조용한 제외 금지).
            'fault_not_applicable': list(report.scan.fault_not_applicable_ids),
        },
        'cells': [_group_to_dict(g) for g in report.cells],
        'pooled': [_group_to_dict(g) for g in report.pooled],
    }


def _fmt_stat(stat: MetricStat) -> str:
    """MetricStat → markdown 셀 문자열. N/A·미정·평균±CI 분기."""
    if not stat.applicable:
        return 'N/A'
    if stat.mean is None:
        return '—'  # 적용 가능하나 표본 0
    if stat.ci95_half is None:
        return f'{stat.mean:.3f} (n={stat.n})'
    return f'{stat.mean:.3f} ± {stat.ci95_half:.3f} (n={stat.n})'


def _format_table(groups: Sequence[GroupAggregate], title: str) -> str:
    """그룹 리스트 → markdown 표 (행 = 그룹, 열 = 메트릭 6종)."""
    header = ['scenario', 'baseline', 'track'] + [
        METRIC_LABELS[m] for m in METRIC_KEYS
    ]
    lines = [f'### {title}', '', '| ' + ' | '.join(header) + ' |']
    lines.append('|' + '|'.join('---' for _ in header) + '|')
    for g in groups:
        row = [
            g.scenario if g.scenario is not None else 'pooled',
            g.baseline,
            g.track,
        ] + [_fmt_stat(g.stats[m]) for m in METRIC_KEYS]
        lines.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(lines)


def format_markdown(report: AggregateReport) -> str:
    """AggregateReport → paper §8 용 markdown 표 (pooled + cells + 제외 보고)."""
    parts = [
        '# 메트릭 집계 (ADR-0032)',
        '',
        f'집계 trial: {report.n_aggregated}',
        '',
        format_bag_status_scan(report.scan),
        '',
        _format_table(report.pooled, 'baseline pooled (시나리오 합산)'),
        '',
        _format_table(report.cells, 'scenario × baseline 셀'),
        '',
        '- V 는 *각 baseline 의 선언 반경* r(c̃) 기준 침입율 (B0=r_max·B1a=r_min·'
        'B1b=r_max·B2+=r(c̃)) — "자기 선언 안전 집합 전방불변성".',
        '- V_floor 는 *물리 하한 r_min* 공통 기준 침입율 — baseline 간 직접 비교 가능'
        '("물리적 최소 안전거리 침범"). B1a 는 V == V_floor.',
        '- SR(작업 성공률)은 Track A(정상)만 — Track B 셀 = N/A (ADR-0032 D3).',
        '- bar_r(과보수성)은 B0(필터 없음, r 부재) = N/A.',
        '- ± = 95% 신뢰구간 반폭(정규 근사). n<2 시 CI 생략.',
        '- fault_not_applicable trial 은 명료화 후퇴로 주입 미정의 — 결함 통계'
        ' 제외, 개수·trial id 는 위 bag_status 집계에 명시 (ADR-0037 amend).',
    ]
    return '\n'.join(parts)


# ----------------------------------------------------------------- CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`eval-aggregate` 콘솔 스크립트 — 격자 집계 → JSON + markdown 산출."""
    parser = argparse.ArgumentParser(
        prog='eval-aggregate',
        description='paper §C 격자 trial bag 집계 → §8 비교 표 (ADR-0032).',
    )
    parser.add_argument(
        '--output-root', required=True,
        help='RunConfig.output_root (예: results/trials).',
    )
    parser.add_argument(
        '--backbone', required=True,
        help='run-level backbone 식별자 (trial bag 경로 정합).',
    )
    parser.add_argument(
        '--standoff-m', type=float, default=DEFAULT_STANDOFF_M,
        help=f'SR vantage standoff [m] (기본 {DEFAULT_STANDOFF_M}).',
    )
    parser.add_argument(
        '--altitude-m', type=float, default=DEFAULT_ALTITUDE_M,
        help=f'SR vantage 고도 [m] (기본 {DEFAULT_ALTITUDE_M}).',
    )
    parser.add_argument(
        '--delta-m', type=float, default=DEFAULT_DELTA_M,
        help=f'SR vantage 도달 허용오차 [m] (기본 {DEFAULT_DELTA_M}, 캘리브레이션 대상).',
    )
    parser.add_argument(
        '--json-out', default=None,
        help='집계 JSON 원장 출력 경로 (미지정 시 미작성).',
    )
    parser.add_argument(
        '--md-out', default=None,
        help='집계 markdown 표 출력 경로 (미지정 시 stdout).',
    )
    args = parser.parse_args(argv)

    report = aggregate_run(
        args.output_root,
        args.backbone,
        standoff_m=args.standoff_m,
        altitude_m=args.altitude_m,
        delta_m=args.delta_m,
    )

    # 무결성 보고 — 조용한 제외 금지 (D6). 항상 stderr 격 stdout 선두 출력.
    print(format_bag_status_scan(report.scan))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report_to_json_dict(report), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        print(f'JSON 원장 작성: {args.json_out}')

    md = format_markdown(report)
    if args.md_out:
        Path(args.md_out).write_text(md, encoding='utf-8')
        print(f'markdown 표 작성: {args.md_out}')
    else:
        print(md)
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
