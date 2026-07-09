"""eval_runner.trial_meta 단위 테스트.

write side (TrialSpec → trial_meta.yaml) ↔ read side (`eval_metrics.trial_meta.load_trial_metadata`)
측 roundtrip 정합 검증. baseline mode case 변환 ('b0' → 'B0') + fault_variant
None → 'none' 정규화 + wall_clock_s invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.b3_context_aug import b3_config
from eval_baselines.b4_full_loop import b4_config
from eval_baselines.schemas import BaselineConfig
from eval_faults.fault_scenario import FaultChannel, FaultScenario
from eval_metrics.trial_meta import load_trial_metadata

from eval_runner.schemas import TrialSpec
from eval_runner.trial_meta import (
    TRIAL_META_FILENAME,
    trial_meta_yaml_dict,
    trial_meta_yaml_path,
    write_trial_meta_yaml,
)


# -------------------------------------------------------------------- fixtures


def _make_none_fault() -> FaultScenario:
    return FaultScenario(
        name='test_none', description='test',
        channel=FaultChannel.NONE, variant=None,
        context_kwargs={}, seed=42,
    )


def _make_hallucination_fault() -> FaultScenario:
    return FaultScenario(
        name='test_h', description='test',
        channel=FaultChannel.HALLUCINATION,
        variant='position_gauss_low',
        context_kwargs={'position_xyz_cm': (1.0, 1.0, 1.0)},
        seed=42,
    )


def _make_trial(
    config: BaselineConfig,
    fault: FaultScenario,
    scenario: str = 'S5',
    seed: int = 12345,
    episode_id: int = 0,
) -> TrialSpec:
    return TrialSpec(
        scenario_id=scenario,
        baseline_config=config,
        fault_scenario=fault,
        episode_id=episode_id,
        seed=seed,
    )


# -------------------------------------------------------------------- trial_meta_yaml_path


class TestTrialMetaYamlPath:
    def test_uses_trial_meta_filename(self) -> None:
        path = trial_meta_yaml_path('/tmp/foo_bag')
        assert path.name == TRIAL_META_FILENAME

    def test_appends_filename_under_bag_dir(self) -> None:
        path = trial_meta_yaml_path('/tmp/foo_bag')
        assert path == Path('/tmp/foo_bag/trial_meta.yaml')

    def test_accepts_path_object(self) -> None:
        path = trial_meta_yaml_path(Path('/tmp/foo'))
        assert path == Path('/tmp/foo/trial_meta.yaml')


# -------------------------------------------------------------------- trial_meta_yaml_dict


class TestTrialMetaYamlDict:
    def test_scenario_propagated(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault(), scenario='S6')
        d = trial_meta_yaml_dict(trial, wall_clock_s=42.0, bag_status='complete')
        assert d['scenario'] == 'S6'

    def test_baseline_lowercase_to_uppercase(self) -> None:
        """BaselineMode.value 'b0' 측 'B0' uppercase 변환 (TrialMetadata 정합)."""
        for cfg_fn, expected in (
            (b0_config, 'B0'),
            (b1a_config, 'B1A'),
            (b1b_config, 'B1B'),
            (b2_config, 'B2'),
            (b3_config, 'B3'),
            (b4_config, 'B4'),
        ):
            trial = _make_trial(cfg_fn(), _make_none_fault())
            d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
            assert d['baseline'] == expected

    def test_fault_class_propagated(self) -> None:
        trial = _make_trial(b0_config(), _make_hallucination_fault())
        d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        assert d['fault_class'] == 'hallucination'

    def test_fault_variant_propagated(self) -> None:
        trial = _make_trial(b0_config(), _make_hallucination_fault())
        d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        assert d['fault_variant'] == 'position_gauss_low'

    def test_none_fault_variant_normalized(self) -> None:
        """fault_scenario.variant=None 측 'none' string 측 정규화."""
        trial = _make_trial(b0_config(), _make_none_fault())
        d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        assert d['fault_variant'] == 'none'

    def test_seed_propagated(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault(), seed=99999)
        d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        assert d['seed'] == 99999

    def test_wall_clock_s_propagated(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        d = trial_meta_yaml_dict(trial, wall_clock_s=12.5, bag_status='complete')
        assert d['wall_clock_s'] == 12.5

    def test_wall_clock_s_zero_rejected(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        with pytest.raises(ValueError, match='wall_clock_s'):
            trial_meta_yaml_dict(trial, wall_clock_s=0.0, bag_status='complete')

    def test_wall_clock_s_negative_rejected(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        with pytest.raises(ValueError, match='wall_clock_s'):
            trial_meta_yaml_dict(trial, wall_clock_s=-1.0, bag_status='complete')

    def test_yaml_dict_has_only_expected_keys(self) -> None:
        """TrialMetadata 측 허용 키 strict 정합 — 추가 key 측 거부."""
        trial = _make_trial(b0_config(), _make_none_fault())
        d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        assert set(d.keys()) == {
            'scenario', 'baseline', 'fault_class', 'fault_variant', 'seed',
            'wall_clock_s', 'bag_status',
        }

    def test_bag_status_propagated(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        for status in ('complete', 'incomplete', 'fault_not_applicable'):
            d = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status=status)
            assert d['bag_status'] == status

    def test_bag_status_unknown_rejected_on_write(self) -> None:
        """write side 측 'unknown' 금지 — read side legacy 분류 전용."""
        trial = _make_trial(b0_config(), _make_none_fault())
        with pytest.raises(ValueError, match='bag_status'):
            trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='unknown')

    def test_bag_status_invalid_rejected(self) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        with pytest.raises(ValueError, match='bag_status'):
            trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='ok')


# -------------------------------------------------------------------- write_trial_meta_yaml


class TestWriteTrialMetaYaml:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        path = tmp_path / 'trial_meta.yaml'
        write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path, bag_status='complete')
        assert path.exists()

    def test_write_then_load_roundtrip(self, tmp_path: Path) -> None:
        """write_trial_meta_yaml → load_trial_metadata (eval_metrics) roundtrip 정합."""
        trial = _make_trial(b2_config(), _make_hallucination_fault(), scenario='S6', seed=777)
        path = tmp_path / 'trial_meta.yaml'
        write_trial_meta_yaml(trial, wall_clock_s=15.5, path=path, bag_status='complete')

        meta = load_trial_metadata(path)
        assert meta.scenario == 'S6'
        assert meta.baseline == 'B2'
        assert meta.fault_class == 'hallucination'
        assert meta.fault_variant == 'position_gauss_low'
        assert meta.seed == 777
        assert meta.wall_clock_s == 15.5
        assert meta.bag_status == 'complete'

    def test_roundtrip_incomplete_bag_status(self, tmp_path: Path) -> None:
        """bag_status='incomplete' 측 roundtrip — 집계 측 명시 보고 입력."""
        trial = _make_trial(b0_config(), _make_none_fault())
        path = tmp_path / 'trial_meta.yaml'
        write_trial_meta_yaml(
            trial, wall_clock_s=10.0, path=path, bag_status='incomplete',
        )
        meta = load_trial_metadata(path)
        assert meta.bag_status == 'incomplete'

    def test_roundtrip_fault_not_applicable_bag_status(self, tmp_path: Path) -> None:
        """bag_status='fault_not_applicable' (제3 범주, ADR-0037 amend) roundtrip
        — write side 허용 + read side TrialMetadata 검증 통과."""
        trial = _make_trial(b2_config(), _make_hallucination_fault())
        path = tmp_path / 'trial_meta.yaml'
        write_trial_meta_yaml(
            trial, wall_clock_s=10.0, path=path, bag_status='fault_not_applicable',
        )
        meta = load_trial_metadata(path)
        assert meta.bag_status == 'fault_not_applicable'

    def test_roundtrip_all_baselines(self, tmp_path: Path) -> None:
        """6 baseline 측 all roundtrip (amendment 19) — uppercase 변환 + TrialMetadata 검증 통과."""
        for cfg_fn, expected_baseline in (
            (b0_config, 'B0'), (b1a_config, 'B1A'), (b1b_config, 'B1B'), (b2_config, 'B2'),
            (b3_config, 'B3'), (b4_config, 'B4'),
        ):
            trial = _make_trial(cfg_fn(), _make_none_fault())
            path = tmp_path / f'meta_{expected_baseline}.yaml'
            write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path, bag_status='complete')
            meta = load_trial_metadata(path)
            assert meta.baseline == expected_baseline

    def test_roundtrip_none_fault(self, tmp_path: Path) -> None:
        """fault_class=none 측 fault_variant='none' 측 TrialMetadata invariant 통과."""
        trial = _make_trial(b0_config(), _make_none_fault())
        path = tmp_path / 'meta.yaml'
        write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path, bag_status='complete')
        meta = load_trial_metadata(path)
        assert meta.fault_class == 'none'
        assert meta.fault_variant == 'none'

    def test_parent_directory_missing_raises(self, tmp_path: Path) -> None:
        trial = _make_trial(b0_config(), _make_none_fault())
        path = tmp_path / 'nonexistent_subdir' / 'trial_meta.yaml'
        with pytest.raises(FileNotFoundError, match='부모 디렉토리'):
            write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path, bag_status='complete')

    def test_block_style_yaml(self, tmp_path: Path) -> None:
        """`yaml.safe_dump` 측 default_flow_style=False — block style 측 가독성."""
        trial = _make_trial(b0_config(), _make_none_fault())
        path = tmp_path / 'meta.yaml'
        write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path, bag_status='complete')
        text = path.read_text(encoding='utf-8')
        # block style 측 'key: value\n' 패턴 — flow style 측 '{key: value, ...}'.
        assert '\n' in text
        assert text.count(':') >= 7  # 7 키 (bag_status 포함)
        assert not text.strip().startswith('{')

    def test_sort_keys_deterministic(self, tmp_path: Path) -> None:
        """`yaml.safe_dump` 측 sort_keys=True 측 deterministic YAML output."""
        trial = _make_trial(b2_config(), _make_hallucination_fault())
        path1 = tmp_path / 'meta1.yaml'
        path2 = tmp_path / 'meta2.yaml'
        write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path1, bag_status='complete')
        write_trial_meta_yaml(trial, wall_clock_s=10.0, path=path2, bag_status='complete')
        assert path1.read_text() == path2.read_text()


# -------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_yaml_dict_deterministic(self) -> None:
        trial = _make_trial(b2_config(), _make_hallucination_fault(), seed=42)
        d1 = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        d2 = trial_meta_yaml_dict(trial, wall_clock_s=10.0, bag_status='complete')
        assert d1 == d2
