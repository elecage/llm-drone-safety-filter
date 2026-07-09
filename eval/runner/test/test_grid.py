"""eval_runner.grid 단위 테스트.

ADR-0025 D3 격자 (4 × 5 × 5 × 10 = 1000 trial) enumeration 검증.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_baselines.schemas import BaselineMode

from eval_runner.grid import (
    BASELINE_HELPERS,
    default_fault_scenario_paths,
    generate_trial_grid,
    resolve_fault_scenario_paths,
    track_b_fault_scenario_paths,
)
from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec


# 본 test module 측 fault_scenario_paths 의존 — eval/faults/scenarios/ 측 5
# YAML (ADR-0025 D5 #5a 잠금) 측 자동 도출.
@pytest.fixture(scope='module')
def fault_paths() -> list[Path]:
    return default_fault_scenario_paths()


class TestBaselineHelpers:
    def test_all_six_modes_mapped(self) -> None:
        """BASELINE_HELPERS 측 6 mode 모두 매핑 (amendment 19) — single source-of-truth."""
        assert set(BASELINE_HELPERS.keys()) == set(BaselineMode)

    def test_helper_returns_config_with_matching_mode(self) -> None:
        for mode, helper in BASELINE_HELPERS.items():
            cfg = helper()
            assert cfg.mode == mode


class TestDefaultFaultScenarioPaths:
    def test_returns_five_yaml(self) -> None:
        """ADR-0025 D5 #5a 잠금 — 5 fault YAML (none + 4 channel) 정합."""
        paths = default_fault_scenario_paths()
        assert len(paths) == 5

    def test_sorted_deterministic(self) -> None:
        """deterministic enumeration 측 sorted 보장."""
        paths1 = default_fault_scenario_paths()
        paths2 = default_fault_scenario_paths()
        assert paths1 == paths2
        assert paths1 == sorted(paths1)

    def test_all_yaml_extension(self) -> None:
        paths = default_fault_scenario_paths()
        for p in paths:
            assert p.suffix == '.yaml'


class TestGridDimensions:
    def test_default_adr0025_grid_600_trial(
        self, fault_paths: list[Path]
    ) -> None:
        """ADR-0025 D3 + ADR-0039 D2 격자 = 2 × 6 × 5 × 10 = 600 trial (거실 S5/S6)."""
        grid = generate_trial_grid(
            scenarios=list(VALID_SCENARIO_IDS),
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        assert len(grid) == 600

    def test_dimensions_product(self, fault_paths: list[Path]) -> None:
        """격자 = |S| × |B| × |F| × N 정확히 만족."""
        scenarios = ['S5', 'S6']
        baselines = [BaselineMode.B0, BaselineMode.B2, BaselineMode.B4]
        grid = generate_trial_grid(
            scenarios=scenarios,
            baseline_modes=baselines,
            fault_scenario_paths=fault_paths,
            n_episodes=3,
        )
        assert len(grid) == 2 * 3 * 5 * 3  # = 90

    def test_all_scenarios_present(self, fault_paths: list[Path]) -> None:
        grid = generate_trial_grid(
            scenarios=list(VALID_SCENARIO_IDS),
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        scenarios_in_grid = {t.scenario_id for t in grid}
        assert scenarios_in_grid == set(VALID_SCENARIO_IDS)

    def test_all_baselines_present(self, fault_paths: list[Path]) -> None:
        grid = generate_trial_grid(
            scenarios=list(VALID_SCENARIO_IDS),
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        modes_in_grid = {t.baseline_config.mode for t in grid}
        assert modes_in_grid == set(BaselineMode)

    def test_all_episode_ids_present(self, fault_paths: list[Path]) -> None:
        grid = generate_trial_grid(
            scenarios=list(VALID_SCENARIO_IDS),
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        episodes_in_grid = {t.episode_id for t in grid}
        assert episodes_in_grid == set(range(10))


class TestGridSeedReproducibility:
    def test_same_grid_same_seeds(self, fault_paths: list[Path]) -> None:
        """동일 입력 측 generate_trial_grid 측 동일 seed list 생성."""
        grid1 = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=[BaselineMode.B0],
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        grid2 = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=[BaselineMode.B0],
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        assert [t.seed for t in grid1] == [t.seed for t in grid2]

    def test_trial_id_uniqueness(self, fault_paths: list[Path]) -> None:
        """1000 trial 측 trial_id 모두 distinct (5 차원 hash 보장)."""
        grid = generate_trial_grid(
            scenarios=list(VALID_SCENARIO_IDS),
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=fault_paths,
            n_episodes=10,
        )
        trial_ids = [t.trial_id for t in grid]
        assert len(set(trial_ids)) == len(trial_ids)


class TestGridReturnsTrialSpec:
    def test_all_elements_are_trial_spec(
        self, fault_paths: list[Path]
    ) -> None:
        grid = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=[BaselineMode.B0],
            fault_scenario_paths=fault_paths,
            n_episodes=2,
        )
        for elem in grid:
            assert isinstance(elem, TrialSpec)


class TestGridValidation:
    def test_empty_scenarios(self, fault_paths: list[Path]) -> None:
        with pytest.raises(ValueError, match='scenarios'):
            generate_trial_grid(
                scenarios=[],
                baseline_modes=list(BaselineMode),
                fault_scenario_paths=fault_paths,
                n_episodes=10,
            )

    def test_empty_baselines(self, fault_paths: list[Path]) -> None:
        with pytest.raises(ValueError, match='baseline_modes'):
            generate_trial_grid(
                scenarios=['S5'],
                baseline_modes=[],
                fault_scenario_paths=fault_paths,
                n_episodes=10,
            )

    def test_empty_fault_paths(self) -> None:
        with pytest.raises(ValueError, match='fault_scenario_paths'):
            generate_trial_grid(
                scenarios=['S5'],
                baseline_modes=list(BaselineMode),
                fault_scenario_paths=[],
                n_episodes=10,
            )

    def test_zero_n_episodes(self, fault_paths: list[Path]) -> None:
        with pytest.raises(ValueError, match='n_episodes'):
            generate_trial_grid(
                scenarios=['S5'],
                baseline_modes=list(BaselineMode),
                fault_scenario_paths=fault_paths,
                n_episodes=0,
            )

    def test_negative_n_episodes(self, fault_paths: list[Path]) -> None:
        with pytest.raises(ValueError, match='n_episodes'):
            generate_trial_grid(
                scenarios=['S5'],
                baseline_modes=list(BaselineMode),
                fault_scenario_paths=fault_paths,
                n_episodes=-1,
            )

    def test_invalid_scenario(self, fault_paths: list[Path]) -> None:
        with pytest.raises(ValueError, match='scenario_id'):
            generate_trial_grid(
                scenarios=['S3'],  # 실외, 제외
                baseline_modes=[BaselineMode.B0],
                fault_scenario_paths=fault_paths,
                n_episodes=1,
            )

    def test_missing_fault_yaml(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate_trial_grid(
                scenarios=['S5'],
                baseline_modes=[BaselineMode.B0],
                fault_scenario_paths=[tmp_path / 'nonexistent.yaml'],
                n_episodes=1,
            )

    def test_default_fault_scenarios_dir_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            default_fault_scenario_paths(root=tmp_path)

    def test_duplicate_channel_variant_rejected(
        self, tmp_path: Path, fault_paths: list[Path]
    ) -> None:
        """PR #121 self-review M-1 — 동일 (channel, variant) 두 YAML 측 차단.

        ADR-0025 D5 #5a 5 fault YAML 측 channel+variant distinct 잠금 정합.
        후속 YAML 추가 측 silent merging 회피.
        """
        # 기존 none_baseline.yaml 측 copy 측 다른 파일명 측 (channel=none,
        # variant=None) 측 중복 (channel, variant) 측 만듦.
        import shutil
        orig = [p for p in fault_paths if p.name == 'none_baseline.yaml'][0]
        dup = tmp_path / 'none_duplicate.yaml'
        shutil.copy(orig, dup)
        with pytest.raises(ValueError, match='channel, variant'):
            generate_trial_grid(
                scenarios=['S5'],
                baseline_modes=[BaselineMode.B0],
                fault_scenario_paths=[orig, dup],
                n_episodes=1,
            )


class TestTrackBFaultScenarios:
    """amendment 20 — Track B(track_b/) fault 분리 + name 해석."""

    def test_track_b_path_present(self) -> None:
        names = [p.name for p in track_b_fault_scenario_paths()]
        assert 'hallucination_position_worst_user_direct.yaml' in names

    def test_broad_default_excludes_track_b(self) -> None:
        """넓은 격자 default 는 track_b 미포함(5종 불변)."""
        broad = {p.name for p in default_fault_scenario_paths()}
        assert 'hallucination_position_worst_user_direct.yaml' not in broad
        assert len(broad) == 5

    def test_resolve_finds_track_b_name(self) -> None:
        resolved = resolve_fault_scenario_paths(
            ['hallucination_position_worst_user_direct'])
        assert resolved[0].name == 'hallucination_position_worst_user_direct.yaml'

    def test_track_b_subgrid_80_trial(self) -> None:
        """{B0,B1A,B1B,B2} × S5/S6 × worst_user_direct × 10 ep = 80 (D-T2 + ADR-0039 D2).

        하한 검증 격자(track_b/)는 worst_user_direct + geofence_out_direct 2종을
        담는다(둘 다 --faults 로 resolve). RQ1 sub-grid 는 worst_user 전용이라
        해당 경로만으로 격자를 구성해 검증한다.
        """
        worst_user = [
            p for p in track_b_fault_scenario_paths()
            if p.name == 'hallucination_position_worst_user_direct.yaml'
        ]
        assert len(worst_user) == 1
        grid = generate_trial_grid(
            ['S5', 'S6'],
            [BaselineMode.B0, BaselineMode.B1A, BaselineMode.B1B, BaselineMode.B2],
            worst_user,
            n_episodes=10,
        )
        assert len(grid) == 80
        assert all(
            t.fault_scenario.variant == 'position_worst_user_direct' for t in grid)

    def test_track_b_dir_includes_c38_geofence_fault(self) -> None:
        """C38(세션 56) — geofence_out_direct 도 track_b/ 에 있어 --faults 로 resolve."""
        names = {p.name for p in track_b_fault_scenario_paths()}
        assert 'hallucination_position_geofence_out_direct.yaml' in names
        resolved = resolve_fault_scenario_paths(
            ['hallucination_position_geofence_out_direct'])
        assert resolved[0].name == 'hallucination_position_geofence_out_direct.yaml'
