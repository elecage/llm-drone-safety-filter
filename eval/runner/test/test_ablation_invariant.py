"""eval_runner.ablation_invariant 단위 테스트.

격자 측 ablation chain invariant (B0→B1·B1→B2·B2→B3·B3→B4 각 step 정확히 1 축
차이) 자동 검증.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from eval_baselines.schemas import BaselineConfig, BaselineMode

from eval_runner.ablation_invariant import (
    ABLATION_CHAIN,
    cell_count,
    check_chain_invariant,
)
from eval_runner.grid import (
    default_fault_scenario_paths,
    generate_trial_grid,
)
from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec


@pytest.fixture(scope='module')
def fault_paths() -> list[Path]:
    return default_fault_scenario_paths()


def _first_fault_yaml(paths: list[Path]) -> Path:
    """PR #121 self-review M-2 정정 — 5 fault YAML 중 *명시적* 첫 후보.

    이전 ``paths[:1]`` 측 sorted glob 측 우연한 알파벳 순서 ('adversarial_…' 첫)
    에 의존. 본 helper 측 'adversarial_geofence.yaml' 측 explicit lookup —
    sort 순서 변경 (e.g., locale) 측 robust.
    """
    target = 'adversarial_geofence.yaml'
    matches = [p for p in paths if p.name == target]
    if not matches:
        raise FileNotFoundError(
            f'{target} 부재 — ADR-0025 D5 #5a 잠금 YAML 측 확인 필요'
        )
    return matches[0]


@pytest.fixture(scope='module')
def full_grid(fault_paths: list[Path]) -> list[TrialSpec]:
    return generate_trial_grid(
        scenarios=list(VALID_SCENARIO_IDS),
        baseline_modes=list(BaselineMode),
        fault_scenario_paths=fault_paths,
        n_episodes=10,
    )


class TestAblationChainDefinition:
    def test_chain_five_steps(self) -> None:
        """B0→B1a→B1b→B2→B3→B4 = 5 step chain (amendment 19)."""
        assert len(ABLATION_CHAIN) == 5

    def test_chain_consecutive(self) -> None:
        """ABLATION_CHAIN 측 인접 baseline pair 만 — gap 없음."""
        modes = list(BaselineMode)
        expected = [(modes[i], modes[i + 1]) for i in range(len(modes) - 1)]
        assert list(ABLATION_CHAIN) == expected


class TestFullGridInvariant:
    def test_default_grid_passes(self, full_grid: list[TrialSpec]) -> None:
        """ADR-0025 D3 + ADR-0039 D2 600 trial 격자 측 invariant 자동 만족."""
        check_chain_invariant(full_grid)  # raises 측 fail

    def test_default_grid_cell_count(
        self, full_grid: list[TrialSpec]
    ) -> None:
        """default 격자 = 2 시나리오 × 5 fault × 10 episode = 100 cell (ADR-0039 거실 S5/S6).
        각 cell 측 6 baseline → 600 trial.
        """
        assert cell_count(full_grid) == 100
        assert len(full_grid) == 100 * 6


class TestSmallGridInvariant:
    def test_subset_grid_passes(self, fault_paths: list[Path]) -> None:
        """1 시나리오 × 6 baseline × 1 fault × 1 episode = 6 trial 격자 측 만족."""
        grid = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=[_first_fault_yaml(fault_paths)],
            n_episodes=1,
        )
        assert len(grid) == 6
        check_chain_invariant(grid)


class TestMissingBaseline:
    def test_grid_without_b4_fails(self, fault_paths: list[Path]) -> None:
        """B4 누락 측 invariant 위반 — 6 baseline 모두 필요."""
        grid = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=[
                BaselineMode.B0, BaselineMode.B1A, BaselineMode.B1B, BaselineMode.B2,
                BaselineMode.B3,
            ],
            fault_scenario_paths=[_first_fault_yaml(fault_paths)],
            n_episodes=1,
        )
        with pytest.raises(AssertionError, match='baseline 누락'):
            check_chain_invariant(grid)

    def test_grid_without_b1b_fails(self, fault_paths: list[Path]) -> None:
        """B1b 누락 측 — chain 측 중간 mode 부재 (amendment 19)."""
        grid = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=[
                BaselineMode.B0, BaselineMode.B1A, BaselineMode.B2,
                BaselineMode.B3, BaselineMode.B4,
            ],
            fault_scenario_paths=[_first_fault_yaml(fault_paths)],
            n_episodes=1,
        )
        with pytest.raises(AssertionError, match='baseline 누락'):
            check_chain_invariant(grid)


class TestDuplicateBaseline:
    def test_duplicate_mode_in_cell_fails(
        self, fault_paths: list[Path]
    ) -> None:
        """동일 cell 측 동일 mode 측 2 trial 측 invariant 위반."""
        grid = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=[_first_fault_yaml(fault_paths)],
            n_episodes=1,
        )
        # 첫 번째 TrialSpec 의 baseline_config 와 동일 mode 측 trial 추가.
        grid_with_dup = list(grid) + [grid[0]]
        with pytest.raises(AssertionError, match='중복'):
            check_chain_invariant(grid_with_dup)


class TestChainAxisDiff:
    def test_b1a_corrupted_config(self, fault_paths: list[Path]) -> None:
        """B1a 측 BaselineConfig 가 *변조* 측 — chain B0→B1a 측 1 축 차이 위반.

        본 test 측 시나리오: 만약 누군가 b1a_config() 측 잘못 변경해 B1a 측
        (tier1_mode='b0', context_aug=True, tier2_enabled=False) 로 만들면
        B0→B1a 측 2 축 차이 → invariant 위반.

        본 test 측 b1a_config 실제 변경 X — TrialSpec 측 baseline_config 만
        교체.
        """
        grid = list(generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=[_first_fault_yaml(fault_paths)],
            n_episodes=1,
        ))
        # B1a TrialSpec 찾아서 baseline_config 변조 (tier1_mode 만 'b0' 같이
        # 만들고 context_aug=True 도 함께 — 2 축 차이 만듦. tier2_enabled 도
        # True 로 해야 BaselineConfig __post_init__ 통과).
        b1a_idx = next(
            i for i, t in enumerate(grid)
            if t.baseline_config.mode == BaselineMode.B1A
        )
        corrupted_config = BaselineConfig(
            mode=BaselineMode.B1A,
            tier1_mode='b0',
            context_aug=True,
            tier2_enabled=True,
        )
        grid[b1a_idx] = replace(grid[b1a_idx], baseline_config=corrupted_config)
        with pytest.raises(AssertionError, match='1 축 차이'):
            check_chain_invariant(grid)


class TestCellCount:
    def test_empty_grid(self) -> None:
        assert cell_count([]) == 0

    def test_subset_cell_count(self, fault_paths: list[Path]) -> None:
        """2 시나리오 × 5 fault × 3 episode = 30 cell."""
        grid = generate_trial_grid(
            scenarios=['S5', 'S6'],
            baseline_modes=list(BaselineMode),
            fault_scenario_paths=fault_paths,
            n_episodes=3,
        )
        assert cell_count(grid) == 2 * 5 * 3  # = 30
