"""eval_runner.panel 단위 테스트 — experiment control panel 백엔드 순수 로직.

격자 미리보기·export·단일 trial env 매핑 + scenario→location 매핑 검증.
host venv pytest (eval/runner/conftest.py 측 eval_baselines·eval_faults 경로 주입).
"""

from __future__ import annotations

import json

import pytest

from eval_runner import panel
from eval_runner.schemas import VALID_SCENARIO_IDS


class TestScenarioLocation:
    def test_mapping_covers_all_valid_scenarios(self) -> None:
        assert set(panel.SCENARIO_LOCATION) == set(VALID_SCENARIO_IDS)

    def test_livingroom_scenarios(self) -> None:
        for sid in ('S5', 'S6'):
            assert panel.scenario_location(sid) == 'livingroom'

    def test_unknown_scenario_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.scenario_location('S3')


class TestBuildOptions:
    def test_has_sections(self) -> None:
        opts = panel.build_options()
        assert set(opts) == {
            'scenarios', 'baselines', 'faults', 'backbones', 'defaults',
        }

    def test_scenarios_carry_location(self) -> None:
        opts = panel.build_options()
        by_id = {s['id']: s['location'] for s in opts['scenarios']}
        assert by_id == {
            'S5': 'livingroom', 'S6': 'livingroom',
        }

    def test_baselines_six_with_axes(self) -> None:
        opts = panel.build_options()
        modes = {b['mode'] for b in opts['baselines']}
        assert modes == {'b0', 'b1a', 'b1b', 'b2', 'b3', 'b4'}
        by_mode = {b['mode']: b for b in opts['baselines']}
        assert by_mode['b0']['tier1_mode'] == 'b0'
        assert by_mode['b1a']['tier1_mode'] == 'b1'
        assert by_mode['b1b']['tier1_mode'] == 'b1_max'
        assert by_mode['b4']['tier1_mode'] == 'b2'
        assert by_mode['b4']['context_aug'] is True
        assert by_mode['b4']['tier2_enabled'] is True

    def test_faults_five_with_channel(self) -> None:
        opts = panel.build_options()
        channels = {f['channel'] for f in opts['faults']}
        assert 'none' in channels
        assert len(opts['faults']) == 5

    def test_default_n_episodes(self) -> None:
        assert panel.build_options()['defaults']['n_episodes'] == 10

    def test_backbones_from_registry(self) -> None:
        opts = panel.build_options()
        ids = {b['id'] for b in opts['backbones']}
        assert 'gpt-4o' in ids
        assert 'gemma-4-e4b' in ids
        assert len(opts['backbones']) >= 5

    def test_default_backbone_present(self) -> None:
        opts = panel.build_options()
        ids = {b['id'] for b in opts['backbones']}
        assert opts['defaults']['backbone'] in ids


class TestRunnerCommand:
    def test_single_backbone(self) -> None:
        cmd = panel.runner_command(
            ['S5'], ['b0'], ['none_baseline'], 10, ['gemma-4-e4b'],
        )
        assert 'eval-runner' in cmd
        assert '--backbone gemma-4-e4b' in cmd
        assert '--scenarios S5' in cmd
        assert 'for bb' not in cmd

    def test_multi_backbone_loop(self) -> None:
        cmd = panel.runner_command(
            ['S5', 'S6'], ['b0', 'b2'], ['none_baseline'], 5,
            ['gemma-4-e4b', 'qwen2.5-vl-7b'],
        )
        assert 'for bb in gemma-4-e4b qwen2.5-vl-7b' in cmd
        assert '--backbone "$bb"' in cmd

    def test_empty_backbones_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.runner_command(['S5'], ['b0'], ['none_baseline'], 1, [])

    def test_invalid_backbone_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.runner_command(['S5'], ['b0'], ['none_baseline'], 1, ['nope-llm'])


class TestBuildGridPreview:
    def test_full_grid_count(self) -> None:
        opts = panel.build_options()
        fault_names = [f['name'] for f in opts['faults']]
        preview = panel.build_grid_preview(
            scenarios=list(VALID_SCENARIO_IDS),
            baselines=['b0', 'b1a', 'b1b', 'b2', 'b3', 'b4'],
            faults=fault_names,
            n_episodes=10,
        )
        assert preview['total'] == 2 * 6 * 5 * 10  # 600 (ADR-0039 거실 S5/S6)
        assert preview['breakdown'] == {
            'scenarios': 2, 'baselines': 6, 'faults': 5, 'episodes': 10,
        }

    def test_subset_count(self) -> None:
        preview = panel.build_grid_preview(
            scenarios=['S5'],
            baselines=['b0', 'b1a', 'b2'],
            faults=['none_baseline'],
            n_episodes=3,
        )
        assert preview['total'] == 1 * 3 * 1 * 3

    def test_locations_derived(self) -> None:
        preview = panel.build_grid_preview(
            scenarios=['S5', 'S6'],
            baselines=['b0'],
            faults=['none_baseline'],
            n_episodes=1,
        )
        assert preview['locations'] == ['livingroom']

    def test_sample_records_shape(self) -> None:
        preview = panel.build_grid_preview(
            scenarios=['S6'],
            baselines=['b0'],
            faults=['none_baseline'],
            n_episodes=1,
            sample_n=5,
        )
        assert len(preview['sample']) == 1
        rec = preview['sample'][0]
        assert rec['scenario_id'] == 'S6'
        assert rec['location'] == 'livingroom'
        assert rec['baseline'] == 'b0'
        assert rec['fault_channel'] == 'none'
        assert isinstance(rec['seed'], int)
        assert 'trial_id' in rec

    def test_sample_truncated(self) -> None:
        preview = panel.build_grid_preview(
            scenarios=['S5'],
            baselines=['b0', 'b1a', 'b1b', 'b2', 'b3', 'b4'],
            faults=['none_baseline'],
            n_episodes=10,
            sample_n=4,
        )
        assert preview['total'] == 60
        assert len(preview['sample']) == 4

    def test_invalid_baseline_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.build_grid_preview(['S5'], ['bX'], ['none_baseline'], 1)

    def test_invalid_fault_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.build_grid_preview(['S5'], ['b0'], ['nonexistent'], 1)

    def test_invalid_scenario_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.build_grid_preview(['S3'], ['b0'], ['none_baseline'], 1)


class TestExportGridJson:
    def test_writes_json_with_meta_and_trials(self, tmp_path) -> None:
        out = tmp_path / 'grid.json'
        result = panel.export_grid_json(
            scenarios=['S5'],
            baselines=['b0', 'b1a'],
            faults=['none_baseline'],
            n_episodes=2,
            output_path=out,
        )
        assert result['total'] == 4
        assert out.is_file()
        payload = json.loads(out.read_text(encoding='utf-8'))
        assert payload['meta']['total'] == 4
        assert payload['meta']['n_episodes'] == 2
        assert len(payload['trials']) == 4
        assert payload['trials'][0]['scenario_id'] == 'S5'

    def test_creates_parent_dir(self, tmp_path) -> None:
        out = tmp_path / 'nested' / 'dir' / 'grid.json'
        panel.export_grid_json(['S5'], ['b0'], ['none_baseline'], 1, out)
        assert out.is_file()


class TestUpShEnvForTrial:
    def test_livingroom_b1a(self) -> None:
        env = panel.up_sh_env_for_trial('S5', 'b1a')
        assert env['SCENARIO'] == 'livingroom'
        assert env['TIER1_MODE'] == 'b1'
        assert env['G2_SCENARIO'] == ''

    def test_livingroom_b1b(self) -> None:
        env = panel.up_sh_env_for_trial('S5', 'b1b')
        assert env['SCENARIO'] == 'livingroom'
        assert env['TIER1_MODE'] == 'b1_max'
        assert env['G2_SCENARIO'] == ''

    def test_b3_b4_map_tier1_b2(self) -> None:
        """B3/B4 측 tier1_mode='b2' (3축 매핑 정합)."""
        assert panel.up_sh_env_for_trial('S6', 'b3')['TIER1_MODE'] == 'b2'
        assert panel.up_sh_env_for_trial('S6', 'b4')['TIER1_MODE'] == 'b2'

    def test_invalid_baseline_raises(self) -> None:
        with pytest.raises(ValueError):
            panel.up_sh_env_for_trial('S5', 'bZ')
