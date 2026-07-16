"""eval_runner.runner 단위 테스트 — 오케스트레이션 코어 (순수).

격자 선택·계획·resume·dry-run·패널 JSON 로드·CLI 파싱 검증. 실행 셸
(run_trial/run_all)은 ROS 2 + sim 의존이라 본 test 측 cover 안 함 (Mac mini).
host venv pytest (eval/runner/conftest.py 측 eval_baselines·eval_faults 경로 주입).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_runner import runner
from eval_runner.runner import RunConfig
from eval_runner.schemas import VALID_SCENARIO_IDS


def _cfg(tmp_path, **kw) -> RunConfig:
    base = dict(
        scenarios=['S5'],
        baselines=['b0'],
        faults=['none_baseline'],
        n_episodes=1,
        output_root=tmp_path / 'trials',
    )
    base.update(kw)
    return RunConfig(**base)


class TestSelectTrials:
    def test_full_grid(self, tmp_path) -> None:
        cfg = _cfg(
            tmp_path,
            scenarios=list(VALID_SCENARIO_IDS),
            baselines=list(runner._ALL_BASELINES),
            faults=runner._default_fault_names(),
            n_episodes=10,
        )
        assert len(runner.select_trials(cfg)) == 600  # ADR-0039 D2 거실 S5/S6

    def test_subset(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, scenarios=['S5', 'S6'], baselines=['b0', 'b2'],
                   faults=['none_baseline'], n_episodes=3)
        assert len(runner.select_trials(cfg)) == 2 * 2 * 1 * 3

    def test_limit(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, scenarios=list(VALID_SCENARIO_IDS),
                   baselines=list(runner._ALL_BASELINES),
                   faults=runner._default_fault_names(), n_episodes=10, limit=7)
        assert len(runner.select_trials(cfg)) == 7

    def test_invalid_baseline(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            runner.select_trials(_cfg(tmp_path, baselines=['bX']))

    def test_invalid_fault(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            runner.select_trials(_cfg(tmp_path, faults=['nope']))

    def test_invalid_scenario(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            runner.select_trials(_cfg(tmp_path, scenarios=['S3']))

    def test_negative_limit(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            runner.select_trials(_cfg(tmp_path, limit=-1))

    def test_confidence_profiles_expand(self, tmp_path) -> None:
        """ADR-0050 D7 — 프로파일 지정 시 격자 ×|profiles|, 각 synthetic:<profile>."""
        cfg = _cfg(tmp_path, scenarios=['S5', 'S6'], baselines=['b0', 'b2'],
                   faults=['none_baseline'], n_episodes=2,
                   confidence_profiles=('c_constant_1', 'c_stall'))
        trials = runner.select_trials(cfg)
        assert len(trials) == 2 * 2 * 1 * 2 * 2  # +프로파일 축
        assert {t.confidence_source for t in trials} == {
            'synthetic:c_constant_1', 'synthetic:c_stall',
        }

    def test_confidence_profiles_default_live(self, tmp_path) -> None:
        """미지정(기본 ()) → 전 cell live, trial_id 접미 없음(기존 격자 불변)."""
        trials = runner.select_trials(_cfg(tmp_path))
        assert all(t.confidence_source == 'live' for t in trials)
        assert all('__c-' not in t.trial_id for t in trials)


class TestPlanJsonCli:
    def test_main_plan_json_threads_confidence_profiles(
        self, tmp_path, capsys,
    ) -> None:
        """main(--plan-json --confidence-profiles) → 확장된 plan JSON 방출(CLI 배선)."""
        rc = runner.main([
            '--plan-json',
            '--scenarios', 'S5',
            '--baselines', 'b2',
            '--faults', 'none_baseline',
            '--confidence-profiles', 'c_constant_1', 'c_constant_mid',
            '--n-episodes', '2',
            '--output-root', str(tmp_path / 'trials'),
        ])
        assert rc == 0
        obj = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        # 1 baseline × 1 fault × 2 ep × 2 profile = 4
        assert len(obj['trials']) == 4
        assert {t['confidence_source'] for t in obj['trials']} == {
            'synthetic:c_constant_1', 'synthetic:c_constant_mid',
        }
        assert all('confidence_source' in t for t in obj['trials'])


class TestTrialBagDir:
    def test_path_includes_backbone_and_trial_id(self, tmp_path) -> None:
        cfg = _cfg(tmp_path)
        trial = runner.select_trials(cfg)[0]
        bd = runner.trial_bag_dir(cfg.output_root, trial, cfg.backbone)
        assert bd == cfg.output_root / cfg.backbone / trial.trial_id


class TestIsTrialComplete:
    def test_missing_meta(self, tmp_path) -> None:
        assert runner.is_trial_complete(tmp_path / 'nope') is False

    def test_present_meta(self, tmp_path) -> None:
        """legacy meta (bag_status 부재) 측 complete — 종전 resume 거동 보존."""
        bd = tmp_path / 'trial'
        bd.mkdir()
        (bd / 'trial_meta.yaml').write_text('scenario: S5\n', encoding='utf-8')
        assert runner.is_trial_complete(bd) is True

    def test_incomplete_bag_status_not_complete(self, tmp_path) -> None:
        bd = tmp_path / 'trial'
        bd.mkdir()
        (bd / 'trial_meta.yaml').write_text(
            'scenario: S5\nbag_status: incomplete\n', encoding='utf-8',
        )
        assert runner.is_trial_complete(bd) is False

    def test_fault_not_applicable_is_complete_marker(self, tmp_path) -> None:
        """fault_not_applicable = resume 재실행 금지 → 완료 marker (ADR-0037 amend)."""
        bd = tmp_path / 'trial'
        bd.mkdir()
        (bd / 'trial_meta.yaml').write_text(
            'scenario: S5\nbag_status: fault_not_applicable\n', encoding='utf-8',
        )
        assert runner.is_trial_complete(bd) is True


class TestTrialCompletionStatus:
    def _write(self, tmp_path, body: str):
        bd = tmp_path / 'trial'
        bd.mkdir()
        (bd / 'trial_meta.yaml').write_text(body, encoding='utf-8')
        return bd

    def test_missing(self, tmp_path) -> None:
        assert runner.trial_completion_status(tmp_path / 'nope') == 'missing'

    def test_complete_explicit(self, tmp_path) -> None:
        bd = self._write(tmp_path, 'scenario: S5\nbag_status: complete\n')
        assert runner.trial_completion_status(bd) == 'complete'

    def test_legacy_missing_key_is_complete(self, tmp_path) -> None:
        bd = self._write(tmp_path, 'scenario: S5\n')
        assert runner.trial_completion_status(bd) == 'complete'

    def test_incomplete(self, tmp_path) -> None:
        bd = self._write(tmp_path, 'bag_status: incomplete\n')
        assert runner.trial_completion_status(bd) == 'incomplete'

    def test_fault_not_applicable(self, tmp_path) -> None:
        """제3 범주 — 별도 상태로 판정 (resume 측 'done' 취급, ADR-0037 amend)."""
        bd = self._write(tmp_path, 'bag_status: fault_not_applicable\n')
        assert runner.trial_completion_status(bd) == 'fault_not_applicable'

    def test_corrupt_yaml_incomplete(self, tmp_path) -> None:
        """meta 손상 측 재실행이 안전한 쪽 — incomplete 분류."""
        bd = self._write(tmp_path, '{unclosed')
        assert runner.trial_completion_status(bd) == 'incomplete'

    def test_non_dict_yaml_incomplete(self, tmp_path) -> None:
        bd = self._write(tmp_path, 'just-a-string')
        assert runner.trial_completion_status(bd) == 'incomplete'


class TestPlanRun:
    def test_all_pending_without_resume(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, n_episodes=2)
        plan = runner.plan_run(cfg)
        assert len(plan) == 2
        assert all(it.status == 'pending' for it in plan)

    def test_resume_marks_done(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, n_episodes=2, resume=True)
        trials = runner.select_trials(cfg)
        # 첫 trial 만 완료 표시
        bd0 = runner.trial_bag_dir(cfg.output_root, trials[0], cfg.backbone)
        bd0.mkdir(parents=True)
        (bd0 / 'trial_meta.yaml').write_text('scenario: S5\n', encoding='utf-8')
        plan = runner.plan_run(cfg)
        statuses = {it.trial_id: it.status for it in plan}
        assert statuses[trials[0].trial_id] == 'done'
        assert statuses[trials[1].trial_id] == 'pending'

    def test_no_resume_ignores_existing_meta(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, n_episodes=1, resume=False)
        trials = runner.select_trials(cfg)
        bd0 = runner.trial_bag_dir(cfg.output_root, trials[0], cfg.backbone)
        bd0.mkdir(parents=True)
        (bd0 / 'trial_meta.yaml').write_text('scenario: S5\n', encoding='utf-8')
        plan = runner.plan_run(cfg)
        assert plan[0].status == 'pending'  # resume=False → 무시

    def test_resume_marks_incomplete_for_rerun(self, tmp_path) -> None:
        """bag_status='incomplete' trial 측 resume 시 'done' 아닌 'incomplete' —
        run_all 측 재실행 대상 (silent drop 방지)."""
        cfg = _cfg(tmp_path, n_episodes=2, resume=True)
        trials = runner.select_trials(cfg)
        bd0 = runner.trial_bag_dir(cfg.output_root, trials[0], cfg.backbone)
        bd0.mkdir(parents=True)
        (bd0 / 'trial_meta.yaml').write_text(
            'bag_status: incomplete\n', encoding='utf-8',
        )
        plan = runner.plan_run(cfg)
        statuses = {it.trial_id: it.status for it in plan}
        assert statuses[trials[0].trial_id] == 'incomplete'
        assert statuses[trials[1].trial_id] == 'pending'

    def test_resume_marks_fault_not_applicable_done(self, tmp_path) -> None:
        """(d) fault_not_applicable trial 측 resume = 'done' — 결정론적 명료화
        후퇴라 재실행해도 동일 (재실행 금지, ADR-0037 amend)."""
        cfg = _cfg(tmp_path, n_episodes=2, resume=True)
        trials = runner.select_trials(cfg)
        bd0 = runner.trial_bag_dir(cfg.output_root, trials[0], cfg.backbone)
        bd0.mkdir(parents=True)
        (bd0 / 'trial_meta.yaml').write_text(
            'bag_status: fault_not_applicable\n', encoding='utf-8',
        )
        plan = runner.plan_run(cfg)
        statuses = {it.trial_id: it.status for it in plan}
        assert statuses[trials[0].trial_id] == 'done'
        assert statuses[trials[1].trial_id] == 'pending'


class TestFormatPlan:
    def test_contains_counts(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, scenarios=['S5', 'S6'], n_episodes=2)
        text = runner.format_plan(runner.plan_run(cfg))
        assert '총 4 trial' in text
        assert 'pending 4' in text

    def test_truncates_preview(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, scenarios=list(VALID_SCENARIO_IDS),
                   baselines=list(runner._ALL_BASELINES),
                   faults=runner._default_fault_names(), n_episodes=10)
        text = runner.format_plan(runner.plan_run(cfg), preview_n=5)
        assert '생략' in text

    def test_incomplete_count_reported(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, n_episodes=2, resume=True)
        trials = runner.select_trials(cfg)
        bd0 = runner.trial_bag_dir(cfg.output_root, trials[0], cfg.backbone)
        bd0.mkdir(parents=True)
        (bd0 / 'trial_meta.yaml').write_text(
            'bag_status: incomplete\n', encoding='utf-8',
        )
        text = runner.format_plan(runner.plan_run(cfg))
        assert 'incomplete(재실행) 1' in text
        assert 'pending 1' in text


class TestLoadGridJson:
    def test_roundtrip_with_panel_export(self, tmp_path) -> None:
        # panel.export_grid_json 측 형식 모사
        payload = {
            'meta': {
                'scenarios': ['S5', 'S6'],
                'baselines': ['b0', 'b1a'],
                'faults': ['none_baseline'],
                'n_episodes': 4,
                'total': 8,
            },
            'trials': [],
        }
        p = tmp_path / 'grid.json'
        p.write_text(json.dumps(payload), encoding='utf-8')
        sel = runner.load_grid_json(p)
        assert sel == {
            'scenarios': ['S5', 'S6'], 'baselines': ['b0', 'b1a'],
            'faults': ['none_baseline'], 'n_episodes': 4,
        }

    def test_missing_meta_raises(self, tmp_path) -> None:
        p = tmp_path / 'bad.json'
        p.write_text(json.dumps({'trials': []}), encoding='utf-8')
        with pytest.raises(KeyError):
            runner.load_grid_json(p)


class TestMainDryRun:
    def test_dry_run_prints_plan(self, tmp_path, capsys) -> None:
        rc = runner.main([
            '--scenarios', 'S5',
            '--baselines', 'b0', 'b1a',
            '--faults', 'none_baseline',
            '--n-episodes', '2',
            '--output-root', str(tmp_path),
            '--dry-run',
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert '총 4 trial' in out

    def test_dry_run_from_grid_json(self, tmp_path, capsys) -> None:
        payload = {'meta': {
            'scenarios': ['S5'], 'baselines': ['b0', 'b1a', 'b2'],
            'faults': ['none_baseline'], 'n_episodes': 1, 'total': 3,
        }, 'trials': []}
        p = tmp_path / 'grid.json'
        p.write_text(json.dumps(payload), encoding='utf-8')
        rc = runner.main(['--grid-json', str(p), '--output-root', str(tmp_path),
                          '--dry-run'])
        assert rc == 0
        assert '총 3 trial' in capsys.readouterr().out

    def test_default_faults_resolve(self, tmp_path, capsys) -> None:
        rc = runner.main(['--scenarios', 'S5', '--baselines', 'b0',
                          '--n-episodes', '1', '--output-root', str(tmp_path),
                          '--dry-run'])
        assert rc == 0
        # default fault 5종 → 5 trial
        assert '총 5 trial' in capsys.readouterr().out


class TestBackbone:
    def test_default_backbone(self, tmp_path) -> None:
        from eval_runner.launch_composition import DEFAULT_BACKBONE
        assert _cfg(tmp_path).backbone == DEFAULT_BACKBONE

    def test_backbone_in_bag_dir_path(self, tmp_path) -> None:
        cfg = _cfg(tmp_path, backbone='gpt-4o')
        trial = runner.select_trials(cfg)[0]
        bd = runner.trial_bag_dir(cfg.output_root, trial, cfg.backbone)
        assert 'gpt-4o' in bd.parts

    def test_cli_backbone_arg(self, tmp_path, capsys) -> None:
        rc = runner.main(['--scenarios', 'S5', '--baselines', 'b0',
                          '--faults', 'none_baseline', '--n-episodes', '1',
                          '--output-root', str(tmp_path),
                          '--backbone', 'qwen2.5-vl-7b', '--dry-run'])
        assert rc == 0
        assert 'qwen2.5-vl-7b' in capsys.readouterr().out


class TestMainScanBags:
    def _write_meta(self, root, backbone, trial_id, body) -> None:
        d = root / backbone / trial_id
        d.mkdir(parents=True)
        (d / 'trial_meta.yaml').write_text(body, encoding='utf-8')

    def test_scan_clean_exit_zero(self, tmp_path, capsys) -> None:
        from eval_runner.launch_composition import DEFAULT_BACKBONE
        self._write_meta(tmp_path, DEFAULT_BACKBONE, 't1', 'bag_status: complete\n')
        rc = runner.main(['--output-root', str(tmp_path), '--scan-bags'])
        assert rc == 0
        out = capsys.readouterr().out
        assert 'complete 1' in out

    def test_scan_incomplete_exit_one_and_ids_listed(self, tmp_path, capsys) -> None:
        """incomplete 존재 측 exit 1 + trial id 명시 — 집계 게이트."""
        from eval_runner.launch_composition import DEFAULT_BACKBONE
        self._write_meta(tmp_path, DEFAULT_BACKBONE, 't_dead', 'bag_status: incomplete\n')
        rc = runner.main(['--output-root', str(tmp_path), '--scan-bags'])
        assert rc == 1
        out = capsys.readouterr().out
        assert 't_dead' in out
        assert '재실행 대상' in out

    def test_scan_respects_backbone_arg(self, tmp_path, capsys) -> None:
        self._write_meta(tmp_path, 'gpt-4o', 't1', 'bag_status: incomplete\n')
        rc = runner.main(['--output-root', str(tmp_path),
                          '--backbone', 'gpt-4o', '--scan-bags'])
        assert rc == 1
        assert 't1' in capsys.readouterr().out

    def test_scan_fault_not_applicable_not_gate_failure(self, tmp_path, capsys) -> None:
        """fault_not_applicable 만 존재 → 게이트 통과(exit 0) + 별도 카운트·id
        명시 보고 (ADR-0037 amend — scan_gate 가 실패로 치지 않음)."""
        from eval_runner.launch_composition import DEFAULT_BACKBONE
        self._write_meta(tmp_path, DEFAULT_BACKBONE, 't_na',
                         'bag_status: fault_not_applicable\n')
        rc = runner.main(['--output-root', str(tmp_path), '--scan-bags'])
        assert rc == 0
        out = capsys.readouterr().out
        assert 'fault_not_applicable 1' in out
        assert 't_na' in out


class TestMainRejudgeBags:
    """--rejudge-bags — 기존 incomplete trial 의 재분류 경로 (ADR-0041 D1
    기존 CLI 확장, 새 스크립트 없음)."""

    _META = (
        'scenario: S5\nbaseline: B2\nfault_class: hallucination\n'
        'fault_variant: target_swap_dangerous\nseed: 7\nwall_clock_s: 42.0\n'
        'bag_status: incomplete\n'
    )

    def _write_reclassifiable_trial(self, root, backbone, trial_id):
        import json as _json
        d = root / backbone / trial_id
        d.mkdir(parents=True)
        (d / 'trial_meta.yaml').write_text(self._META, encoding='utf-8')
        # σ_raw 0 sample bag (dispatch 토픽 부재) + 전 호출 ask_user JSONL.
        entries = '\n'.join(
            (
                '    - topic_metadata:\n'
                f'        name: {name}\n'
                '        type: std_msgs/msg/String\n'
                '        serialization_format: cdr\n'
                f'      message_count: {count}'
            )
            for name, count in {
                '/fmu/out/vehicle_local_position_v1': 100,
                '/cmd/trajectory_setpoint_safe': 200,
                '/intent/estimator/report': 50,
            }.items()
        )
        (d / 'metadata.yaml').write_text(
            'rosbag2_bagfile_information:\n'
            '  version: 5\n'
            '  storage_identifier: sqlite3\n'
            '  message_count: 350\n'
            '  topics_with_message_count:\n'
            f'{entries}\n',
            encoding='utf-8',
        )
        (d / 'cloud_llm_gpt-4o.jsonl').write_text(
            _json.dumps({'skills': ['ask_user']}) + '\n', encoding='utf-8',
        )
        return d

    def test_rejudge_reclassifies_and_gate_passes(self, tmp_path, capsys) -> None:
        d = self._write_reclassifiable_trial(tmp_path, 'gpt-4o', 't_swap')
        rc = runner.main(['--output-root', str(tmp_path),
                          '--backbone', 'gpt-4o',
                          '--scan-bags', '--rejudge-bags'])
        assert rc == 0  # incomplete 소거 → 게이트 통과
        out = capsys.readouterr().out
        assert '[rejudge] t_swap: incomplete → fault_not_applicable' in out
        assert 'fault_not_applicable 1' in out
        import yaml as _yaml
        raw = _yaml.safe_load((d / 'trial_meta.yaml').read_text(encoding='utf-8'))
        assert raw['bag_status'] == 'fault_not_applicable'

    def test_rejudge_leaves_true_incomplete_and_gate_fails(
        self, tmp_path, capsys,
    ) -> None:
        """JSONL 부재 incomplete 는 재분류 불가 → 게이트 여전히 exit 1."""
        d = tmp_path / 'gpt-4o' / 't_dead'
        d.mkdir(parents=True)
        (d / 'trial_meta.yaml').write_text(self._META, encoding='utf-8')
        rc = runner.main(['--output-root', str(tmp_path),
                          '--backbone', 'gpt-4o',
                          '--scan-bags', '--rejudge-bags'])
        assert rc == 1
        out = capsys.readouterr().out
        assert '재분류 0 trial' in out
        assert 't_dead' in out


class TestResolveFaultScenarioPaths:
    def test_resolves_known_names(self) -> None:
        from eval_runner.grid import resolve_fault_scenario_paths
        paths = resolve_fault_scenario_paths(['none_baseline'])
        assert len(paths) == 1
        assert paths[0].suffix == '.yaml'

    def test_unknown_name_raises(self) -> None:
        from eval_runner.grid import resolve_fault_scenario_paths
        with pytest.raises(ValueError):
            resolve_fault_scenario_paths(['nope'])
