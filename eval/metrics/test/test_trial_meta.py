"""eval_metrics.trial_meta 단위 테스트 — YAML loader + invariant."""

from __future__ import annotations

import pytest

from eval_metrics.schemas import TrialMetadata
from eval_metrics.trial_meta import load_trial_metadata


# ----------------------------------------------------------- loader


class TestLoadTrialMetadata:
    def test_loads_valid_yaml(self, tmp_path):
        path = tmp_path / 'meta.yaml'
        path.write_text(
            'scenario: S5\n'
            'baseline: B2\n'
            "fault_class: hallucination\n"
            "fault_variant: position_noise_gauss_low\n"
            'seed: 42\n'
            'wall_clock_s: 75.5\n',
            encoding='utf-8',
        )
        meta = load_trial_metadata(path)
        assert isinstance(meta, TrialMetadata)
        assert meta.scenario == 'S5'
        assert meta.baseline == 'B2'
        assert meta.fault_class == 'hallucination'
        assert meta.fault_variant == 'position_noise_gauss_low'
        assert meta.seed == 42
        assert meta.wall_clock_s == 75.5

    def test_loads_none_fault_class(self, tmp_path):
        path = tmp_path / 'meta.yaml'
        path.write_text(
            'scenario: S6\n'
            'baseline: B0\n'
            'fault_class: none\n'
            "fault_variant: ''\n"
            'seed: 7\n'
            'wall_clock_s: 60.0\n',
            encoding='utf-8',
        )
        meta = load_trial_metadata(path)
        assert meta.fault_class == 'none'
        assert meta.fault_variant == ''

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_trial_metadata(tmp_path / 'nonexistent.yaml')

    def test_root_not_dict_rejected(self, tmp_path):
        path = tmp_path / 'bad.yaml'
        path.write_text('- list_root\n', encoding='utf-8')
        with pytest.raises(ValueError, match='YAML root'):
            load_trial_metadata(path)

    def test_missing_required_key_rejected(self, tmp_path):
        """필수 key 부재 — wall_clock_s 측 누락."""
        path = tmp_path / 'bad.yaml'
        path.write_text(
            'scenario: S5\nbaseline: B0\nfault_class: none\n'
            "fault_variant: ''\nseed: 42\n",
            encoding='utf-8',
        )
        with pytest.raises(KeyError, match='wall_clock_s'):
            load_trial_metadata(path)

    def test_unknown_yaml_key_rejected(self, tmp_path):
        """schema 외 키 — typo `wallclock_s` 측 silent default 회피."""
        path = tmp_path / 'bad.yaml'
        path.write_text(
            'scenario: S5\nbaseline: B0\nfault_class: none\n'
            "fault_variant: ''\nseed: 42\nwall_clock_s: 60.0\n"
            'wallclock_s: 99.0\n',  # typo
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='unknown YAML keys'):
            load_trial_metadata(path)

    def test_invalid_scenario_rejected(self, tmp_path):
        path = tmp_path / 'bad.yaml'
        path.write_text(
            'scenario: S99\nbaseline: B0\nfault_class: none\n'
            "fault_variant: ''\nseed: 42\nwall_clock_s: 60.0\n",
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='scenario'):
            load_trial_metadata(path)

    def test_invalid_fault_variant_for_class_rejected(self, tmp_path):
        """fault_class='none' 측 variant 측 'unexpected' 거부 (TrialMetadata invariant)."""
        path = tmp_path / 'bad.yaml'
        path.write_text(
            'scenario: S5\nbaseline: B0\nfault_class: none\n'
            'fault_variant: unexpected\nseed: 42\nwall_clock_s: 60.0\n',
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='fault_class=none'):
            load_trial_metadata(path)


# ----------------------------------------------------------- bag_status (선택 키)


class TestLoadBagStatus:
    _BASE = (
        'scenario: S5\nbaseline: B0\nfault_class: none\n'
        "fault_variant: ''\nseed: 42\nwall_clock_s: 60.0\n"
    )

    def test_legacy_missing_key_loads_unknown(self, tmp_path):
        """bag_status 부재(legacy meta) 측 'unknown' — silent 'complete' 승격 금지."""
        path = tmp_path / 'meta.yaml'
        path.write_text(self._BASE, encoding='utf-8')
        assert load_trial_metadata(path).bag_status == 'unknown'

    def test_complete_loaded(self, tmp_path):
        path = tmp_path / 'meta.yaml'
        path.write_text(self._BASE + 'bag_status: complete\n', encoding='utf-8')
        assert load_trial_metadata(path).bag_status == 'complete'

    def test_incomplete_loaded(self, tmp_path):
        path = tmp_path / 'meta.yaml'
        path.write_text(self._BASE + 'bag_status: incomplete\n', encoding='utf-8')
        assert load_trial_metadata(path).bag_status == 'incomplete'

    def test_invalid_value_rejected(self, tmp_path):
        path = tmp_path / 'meta.yaml'
        path.write_text(self._BASE + 'bag_status: maybe\n', encoding='utf-8')
        with pytest.raises(ValueError, match='bag_status'):
            load_trial_metadata(path)
