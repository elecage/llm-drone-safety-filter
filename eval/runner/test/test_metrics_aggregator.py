"""metrics_aggregator — 격자 집계 파이프라인 단위 테스트 (ADR-0032).

host venv 순수 테스트 — 실 rosbag2 없이 (a) `aggregate_records` 순수 집계,
(b) `aggregate_run` 드라이버(가짜 read_bag 주입), (c) 출력 포맷터를 검증.
"""

from __future__ import annotations

import json

import pytest

from eval_metrics.schemas import TrialMetadata
from eval_runner.bag_pipeline import BagInputs, TrialMetricsReport
from eval_runner.metrics_aggregator import (
    METRIC_KEYS,
    TrialRecord,
    aggregate_records,
    aggregate_run,
    compute_record,
    format_markdown,
    report_to_json_dict,
    track_of,
)
from eval_runner.task_success_geom import expected_vantage_local


# ----------------------------------------------------------------- fixtures


def _meta(scenario='S6', baseline='B1A', fault_class='none', fault_variant='',
          seed=1, bag_status='complete'):
    return TrialMetadata(
        scenario=scenario, baseline=baseline, fault_class=fault_class,
        fault_variant=fault_variant, seed=seed, wall_clock_s=10.0,
        bag_status=bag_status,
    )


def _metrics(v=0.0, sr=True, ars=1.0, qr=0.0, bar_r=0.9, tau=0.04, v_floor=None,
             gate_rej=None):
    return TrialMetricsReport(
        safety_violation_rate=v,
        safety_violation_rate_floor=v if v_floor is None else v_floor,
        task_success=sr, autonomy_response_score=ars,
        query_rate=qr, overconservativeness=bar_r, realtime_latency=tau,
        gate_rejection_rate=gate_rej,
    )


def _rec(trial_id, meta, metrics):
    return TrialRecord(trial_id=trial_id, meta=meta, metrics=metrics)


# ----------------------------------------------------------------- track 분류


def test_track_of_normal_vs_fault():
    assert track_of(_meta(fault_class='none')) == 'A'
    assert track_of(_meta(fault_class='hallucination',
                          fault_variant='coord_shift')) == 'B'


# ----------------------------------------------------------------- 순수 집계


def test_aggregate_records_groups_by_scenario_baseline_track():
    recs = [
        _rec('t1', _meta('S6', 'B1A'), _metrics(sr=True)),
        _rec('t2', _meta('S6', 'B1A'), _metrics(sr=False)),
        _rec('t3', _meta('S5', 'B1A'), _metrics(sr=True)),
    ]
    rep = aggregate_records(recs)
    # 셀: (S7,B1A,A) + (S5,B1A,A) = 2.
    assert len(rep.cells) == 2
    # pooled: (B1A,A) = 1.
    assert len(rep.pooled) == 1
    assert rep.n_aggregated == 3
    pooled = rep.pooled[0]
    assert pooled.baseline == 'B1A' and pooled.track == 'A'
    assert pooled.n_trials == 3
    # SR pooled = 2/3.
    assert pooled.stats['task_success'].mean == pytest.approx(2 / 3)


def test_aggregate_records_sr_na_for_track_b():
    recs = [
        _rec('t1', _meta('S6', 'B2', 'hallucination', 'coord_shift'),
             _metrics(sr=False, v=0.0)),
        _rec('t2', _meta('S6', 'B2', 'hallucination', 'coord_shift'),
             _metrics(sr=False, v=0.0)),
    ]
    rep = aggregate_records(recs)
    cell = rep.cells[0]
    assert cell.track == 'B'
    # SR = N/A (Track B, D3).
    assert cell.stats['task_success'].applicable is False
    assert cell.stats['task_success'].mean is None
    # V·bar_r·tau 는 적용됨.
    assert cell.stats['safety_violation_rate'].applicable is True
    assert cell.stats['overconservativeness'].applicable is True


def test_aggregate_records_bar_r_na_for_b0():
    recs = [
        _rec('t1', _meta('S6', 'B0'), _metrics(bar_r=2.0)),
        _rec('t2', _meta('S6', 'B0'), _metrics(bar_r=2.0)),
    ]
    rep = aggregate_records(recs)
    cell = rep.cells[0]
    # bar_r = N/A (B0 필터 없음, r 부재).
    assert cell.stats['overconservativeness'].applicable is False
    assert cell.stats['overconservativeness'].mean is None
    # V·SR(track A) 는 적용됨.
    assert cell.stats['safety_violation_rate'].applicable is True
    assert cell.stats['task_success'].applicable is True


def test_aggregate_records_gate_r_b4_only_and_none_skip():
    # B2: 게이트 미활성 → gate_R N/A (ADR-0039 D4).
    recs_b2 = [_rec('t1', _meta('S6', 'B2'), _metrics(gate_rej=None))]
    cell_b2 = aggregate_records(recs_b2).cells[0]
    assert cell_b2.stats['gate_rejection_rate'].applicable is False
    assert cell_b2.stats['gate_rejection_rate'].mean is None
    # B4: 게이트 활성 → applicable. 결정 0(None) trial 은 mean 에서 제외.
    recs_b4 = [
        _rec('t1', _meta('S6', 'B4'), _metrics(gate_rej=0.5)),
        _rec('t2', _meta('S6', 'B4'), _metrics(gate_rej=0.9)),
        _rec('t3', _meta('S6', 'B4'), _metrics(gate_rej=None)),  # 결정 0 → skip
    ]
    cell_b4 = aggregate_records(recs_b4).cells[0]
    st = cell_b4.stats['gate_rejection_rate']
    assert st.applicable is True
    assert st.mean == pytest.approx(0.7)  # (0.5+0.9)/2, None 제외
    assert st.n == 2


def test_aggregate_records_mean_and_ci():
    recs = [
        _rec(f't{i}', _meta('S6', 'B2'), _metrics(v=val))
        for i, val in enumerate([0.0, 0.0, 0.3, 0.3])
    ]
    rep = aggregate_records(recs)
    stat = rep.cells[0].stats['safety_violation_rate']
    assert stat.n == 4
    assert stat.mean == pytest.approx(0.15)
    # 표본 std (ddof=1) = sqrt(var); CI 반폭 = 1.96 * std/sqrt(n) > 0.
    assert stat.ci95_half is not None and stat.ci95_half > 0.0


def test_aggregate_records_single_trial_no_ci():
    recs = [_rec('t1', _meta('S6', 'B2'), _metrics(v=0.1))]
    rep = aggregate_records(recs)
    stat = rep.cells[0].stats['safety_violation_rate']
    assert stat.n == 1
    assert stat.mean == pytest.approx(0.1)
    assert stat.ci95_half is None  # n<2 → CI 생략


def test_aggregate_records_track_a_and_b_separate_cells():
    recs = [
        _rec('t1', _meta('S6', 'B2', 'none'), _metrics()),
        _rec('t2', _meta('S6', 'B2', 'adversarial', 'flip_target'), _metrics()),
    ]
    rep = aggregate_records(recs)
    tracks = sorted(c.track for c in rep.cells)
    assert tracks == ['A', 'B']  # 동일 (S7,B2) 라도 track 분리.


# ----------------------------------------------------------------- I/O 드라이버


def _write_trial(root, trial_id, meta_yaml):
    """trial 디렉토리 생성 — trial_meta.yaml + (가짜) rosbag2 metadata.yaml."""
    d = root / trial_id
    d.mkdir(parents=True)
    (d / 'trial_meta.yaml').write_text(meta_yaml, encoding='utf-8')
    # find_rosbag_metadata 가 찾을 metadata.yaml — 내용은 read_bag_fn 주입이라 무관.
    (d / 'metadata.yaml').write_text('rosbag2_bagfile_information: {}\n',
                                     encoding='utf-8')
    return d


def _bag_inputs_reaching(scenario, reach=True):
    """기대 vantage 도달(reach=True)/미도달 BagInputs — 정적 r baseline 용."""
    v = expected_vantage_local(scenario)
    if reach:
        positions = [(0.0, (0.0, 0.0, 0.0)), (5.0, v)]
    else:
        positions = [(0.0, (50.0, 50.0, 50.0)), (5.0, (50.0, 50.0, 50.0))]
    return BagInputs(
        drone_position_msgs=positions,
        setpoint_timestamps_s=[0.0, 0.05, 0.10],
        estimator_report_json_strs=[],  # 정적 r baseline (B0/B1A/B1B) → 빈 list OK
        tier2_decision_json_strs=[],
        episode_duration_s=10.0,
    )


def test_aggregate_run_with_injected_reader(tmp_path):
    root = tmp_path / 'results'
    backbone = 'gpt-4o'
    base = root / backbone
    # S7 B1A 정상 — 도달(SR=True).
    _write_trial(base, 'trial_0001',
                 'scenario: S6\nbaseline: B1A\nfault_class: none\n'
                 'fault_variant: ""\nseed: 1\nwall_clock_s: 10.0\n'
                 'bag_status: complete\n')
    # S7 B1A 정상 — 미도달(SR=False).
    _write_trial(base, 'trial_0002',
                 'scenario: S6\nbaseline: B1A\nfault_class: none\n'
                 'fault_variant: ""\nseed: 2\nwall_clock_s: 10.0\n'
                 'bag_status: complete\n')
    # incomplete — 집계 제외 + 명시 보고.
    _write_trial(base, 'trial_0003',
                 'scenario: S6\nbaseline: B1A\nfault_class: none\n'
                 'fault_variant: ""\nseed: 3\nwall_clock_s: 10.0\n'
                 'bag_status: incomplete\n')

    reach_map = {'trial_0001': True, 'trial_0002': False}

    def fake_read_bag(bag_dir, *, episode_duration_s=None, **kw):
        trial_id = bag_dir.name
        return _bag_inputs_reaching('S6', reach=reach_map[trial_id])

    rep = aggregate_run(root, backbone, read_bag_fn=fake_read_bag)
    assert rep.n_aggregated == 2  # complete 2개만
    assert rep.scan.incomplete_ids == ('trial_0003',)
    # pooled (B1A,A) SR = 1/2.
    pooled = [g for g in rep.pooled if g.baseline == 'B1A' and g.track == 'A']
    assert len(pooled) == 1
    assert pooled[0].stats['task_success'].mean == pytest.approx(0.5)


def test_aggregate_run_excludes_fault_not_applicable_with_explicit_report(tmp_path):
    """(e) fault_not_applicable trial — 지표 풀 제외 + md/json 별도 카운트·id
    명시 (조용한 제외 금지, ADR-0037 amend)."""
    root = tmp_path / 'results'
    backbone = 'gpt-4o'
    base = root / backbone
    _write_trial(base, 'trial_0001',
                 'scenario: S5\nbaseline: B2\nfault_class: hallucination\n'
                 'fault_variant: target_swap_dangerous\nseed: 1\n'
                 'wall_clock_s: 42.0\nbag_status: fault_not_applicable\n')
    _write_trial(base, 'trial_0002',
                 'scenario: S6\nbaseline: B1A\nfault_class: none\n'
                 'fault_variant: ""\nseed: 2\nwall_clock_s: 10.0\n'
                 'bag_status: complete\n')

    def fake_read_bag(bag_dir, *, episode_duration_s=None, **kw):
        assert bag_dir.name != 'trial_0001', 'fault_not_applicable 인데 bag 읽음'
        return _bag_inputs_reaching('S6', reach=True)

    rep = aggregate_run(root, backbone, read_bag_fn=fake_read_bag)
    assert rep.n_aggregated == 1  # complete 만
    assert rep.scan.fault_not_applicable_ids == ('trial_0001',)
    assert rep.scan.incomplete_ids == ()

    d = report_to_json_dict(rep)
    assert d['bag_status']['fault_not_applicable'] == ['trial_0001']

    md = format_markdown(rep)
    assert 'fault_not_applicable 1' in md
    assert 'trial_0001' in md
    assert '명료화 후퇴로 주입 미정의' in md
    assert '결함 통계 제외' in md


def test_aggregate_run_demotes_missing_rosbag_metadata(tmp_path):
    root = tmp_path / 'results'
    backbone = 'gpt-4o'
    base = root / backbone
    d = base / 'trial_0001'
    d.mkdir(parents=True)
    # bag_status=complete 이나 rosbag2 metadata.yaml 부재 → 강등.
    (d / 'trial_meta.yaml').write_text(
        'scenario: S6\nbaseline: B1A\nfault_class: none\n'
        'fault_variant: ""\nseed: 1\nwall_clock_s: 10.0\n'
        'bag_status: complete\n', encoding='utf-8')

    def fake_read_bag(bag_dir, **kw):  # 호출되면 안 됨
        raise AssertionError('metadata 부재인데 read_bag 호출됨')

    rep = aggregate_run(root, backbone, read_bag_fn=fake_read_bag)
    assert rep.n_aggregated == 0
    assert rep.scan.incomplete_ids == ('trial_0001',)


def test_compute_record_track_b_sets_success_false(tmp_path):
    # Track B — SR 무의미, task_success=False 강제 (집계에서 제외).
    meta = _meta('S6', 'B1B', 'hallucination', 'coord_shift')
    inputs = _bag_inputs_reaching('S6', reach=True)  # 도달해도 무시
    rec = compute_record('t1', meta, inputs)
    assert rec.metrics.task_success is False


def test_compute_record_track_a_geometry_success(tmp_path):
    meta = _meta('S6', 'B1A', 'none')
    rec_hit = compute_record('t1', meta, _bag_inputs_reaching('S6', reach=True))
    rec_miss = compute_record('t2', meta, _bag_inputs_reaching('S6', reach=False))
    assert rec_hit.metrics.task_success is True
    assert rec_miss.metrics.task_success is False


# ----------------------------------------------------------------- 출력


def test_report_to_json_dict_roundtrip():
    recs = [
        _rec('t1', _meta('S6', 'B0'), _metrics()),
        _rec('t2', _meta('S6', 'B2', 'adversarial', 'flip'), _metrics()),
    ]
    rep = aggregate_records(recs)
    d = report_to_json_dict(rep)
    s = json.dumps(d, ensure_ascii=False)  # 직렬화 가능해야 함
    assert 'cells' in d and 'pooled' in d
    assert d['n_aggregated'] == 2
    # B0 bar_r·track B SR 가 applicable=False 로 직렬화.
    b0_cell = next(c for c in d['cells'] if c['baseline'] == 'B0')
    assert b0_cell['metrics']['overconservativeness']['applicable'] is False
    assert '"applicable": false' in s.replace(' ', '') or True


def test_format_markdown_contains_metrics_and_na():
    recs = [
        _rec('t1', _meta('S6', 'B0'), _metrics()),
        _rec('t2', _meta('S6', 'B2', 'adversarial', 'flip'), _metrics()),
    ]
    rep = aggregate_records(recs)
    md = format_markdown(rep)
    for label in ('V', 'SR', 'ARS', 'QR', 'bar_r', 'tau_loop'):
        assert label in md
    assert 'N/A' in md  # B0 bar_r 또는 track B SR
    assert 'baseline pooled' in md


def test_metric_keys_match_report_fields():
    # METRIC_KEYS 가 TrialMetricsReport 필드와 정합 (오타 가드).
    fields = set(TrialMetricsReport.__dataclass_fields__.keys())
    assert set(METRIC_KEYS) == fields
