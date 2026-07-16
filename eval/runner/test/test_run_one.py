"""eval_runner.run_one + grid.build_trial_spec + runner.plan_to_json_obj 단위 테스트.

host-driven 오케스트레이션(ADR-0030 D5/D6)의 *핵심 불변식* 검증: host 가 넘긴
trial 좌표(scenario·baseline·fault name·episode)로 컨테이너가 *동일* TrialSpec 을
재구성한다(seed·trial_id 일치). 이 불변식이 깨지면 host plan 과 컨테이너 실행이
다른 trial 을 가리켜 격자 무결성이 무너진다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_baselines.schemas import BaselineMode

from eval_runner.grid import (
    build_trial_spec,
    default_fault_scenario_paths,
    generate_trial_grid,
)
from eval_runner.run_one import build_trial_from_coords
from eval_runner.runner import (
    RunConfig,
    plan_run,
    plan_to_json_obj,
)
from eval_runner.schemas import VALID_SCENARIO_IDS

from eval_faults.fault_scenario import load_fault_scenario


@pytest.fixture(scope='module')
def fault_paths() -> list[Path]:
    return default_fault_scenario_paths()


@pytest.fixture(scope='module')
def fault_names(fault_paths: list[Path]) -> list[str]:
    return [load_fault_scenario(p).name for p in fault_paths]


class TestBuildTrialSpecMatchesGrid:
    def test_build_trial_spec_equals_grid_cell(
        self, fault_paths: list[Path]
    ) -> None:
        """build_trial_spec 단일 cell == generate_trial_grid 대응 cell (전 필드)."""
        grid = generate_trial_grid(
            scenarios=['S5'],
            baseline_modes=[BaselineMode.B2],
            fault_scenario_paths=fault_paths,
            n_episodes=3,
        )
        for trial in grid:
            rebuilt = build_trial_spec(
                trial.scenario_id,
                trial.baseline_config.mode,
                trial.fault_scenario,
                trial.episode_id,
            )
            assert rebuilt == trial

    def test_seed_order_independent(self, fault_paths: list[Path]) -> None:
        """seed 는 격자 순서 독립 — 작은 격자/큰 격자에서 같은 좌표 → 같은 seed."""
        small = generate_trial_grid(
            ['S6'], [BaselineMode.B0], fault_paths[:1], 2,
        )
        big = generate_trial_grid(
            list(VALID_SCENARIO_IDS), list(BaselineMode), fault_paths, 10,
        )
        big_by_id = {t.trial_id: t for t in big}
        for t in small:
            assert t.seed == big_by_id[t.trial_id].seed


class TestBuildTrialFromCoords:
    def test_matches_grid_for_same_coords(
        self, fault_names: list[str]
    ) -> None:
        """좌표 재구성 == 격자 enumeration (trial_id + seed 일치)."""
        grid = generate_trial_grid(
            scenarios=['S6'],
            baseline_modes=[BaselineMode.B4],
            fault_scenario_paths=default_fault_scenario_paths(),
            n_episodes=2,
        )
        for trial in grid:
            rebuilt = build_trial_from_coords(
                scenario=trial.scenario_id,
                baseline=trial.baseline_config.mode.value,
                fault=trial.fault_scenario.name,
                episode=trial.episode_id,
            )
            assert rebuilt.trial_id == trial.trial_id
            assert rebuilt.seed == trial.seed
            assert rebuilt == trial

    def test_invalid_baseline_raises(self, fault_names: list[str]) -> None:
        with pytest.raises(ValueError):
            build_trial_from_coords('S5', 'b9', fault_names[0], 0)

    def test_invalid_fault_raises(self) -> None:
        with pytest.raises(ValueError):
            build_trial_from_coords('S5', 'b2', 'no_such_fault', 0)

    def test_invalid_scenario_raises(self, fault_names: list[str]) -> None:
        with pytest.raises(ValueError):
            build_trial_from_coords('S1', 'b2', fault_names[0], 0)


class TestPlanToJsonRoundTrip:
    def test_plan_json_coords_reconstruct_identical_trial(self) -> None:
        """★ 핵심 불변식 — plan JSON 좌표 → build_trial_from_coords → 동일 trial.

        host run_grid.py 가 --plan-json 좌표를 eval-runner-one 에 넘겨 재구성하는
        경로 전체를 host venv 에서 모사. trial_id 가 일치해야 bag_dir·resume·scan 이
        같은 trial 을 가리킨다.
        """
        config = RunConfig(
            scenarios=['S5', 'S6'],
            baselines=['b0', 'b2'],
            faults=[load_fault_scenario(p).name
                    for p in default_fault_scenario_paths()],
            n_episodes=2,
            output_root=Path('results/trials'),
        )
        plan = plan_run(config)
        obj = plan_to_json_obj(plan)
        assert len(obj['trials']) == len(plan)
        for item, jt in zip(plan, obj['trials']):
            assert jt['trial_id'] == item.trial_id
            assert jt['status'] == item.status
            rebuilt = build_trial_from_coords(
                scenario=jt['scenario'],
                baseline=jt['baseline'],
                fault=jt['fault'],
                episode=jt['episode'],
            )
            assert rebuilt.trial_id == item.trial_id
            assert rebuilt == item.trial

    def test_plan_json_status_fields_present(self) -> None:
        config = RunConfig(
            scenarios=['S6'],
            baselines=['b1a'],
            faults=[load_fault_scenario(default_fault_scenario_paths()[0]).name],
            n_episodes=1,
            output_root=Path('results/trials'),
        )
        obj = plan_to_json_obj(plan_run(config))
        jt = obj['trials'][0]
        assert set(jt.keys()) == {
            'trial_id', 'status', 'scenario', 'baseline', 'fault',
            'episode', 'confidence_source', 'bag_dir',
        }
        assert jt['scenario'] == 'S6'
        assert jt['baseline'] == 'b1a'
        assert jt['confidence_source'] == 'live'


class TestConfidenceProfileExpansion:
    """ADR-0050 D7 — --confidence-profiles 격자 확장 + 좌표 왕복."""

    def _config(self, profiles, faults):
        return RunConfig(
            scenarios=['S5'],
            baselines=['b2'],
            faults=faults,
            n_episodes=2,
            output_root=Path('results/track_b'),
            confidence_profiles=profiles,
        )

    def test_empty_profiles_keeps_live_grid(self, fault_names: list[str]) -> None:
        """미지정(빈값) → 확장 없음, 전 cell confidence_source='live' (기존 격자 불변)."""
        plan = plan_run(self._config((), fault_names[:1]))
        assert all(it.trial.confidence_source == 'live' for it in plan)
        # live trial_id 는 __c- 접미 없음.
        assert all('__c-' not in it.trial_id for it in plan)

    def test_profiles_expand_grid(self, fault_names: list[str]) -> None:
        """프로파일 3종 → cell 수 ×3, 각 synthetic:<profile>, seed 는 프로파일 불변."""
        profiles = ('c_constant_1', 'c_constant_mid', 'c_stall')
        base = plan_run(self._config((), fault_names[:1]))
        expanded = plan_run(self._config(profiles, fault_names[:1]))
        assert len(expanded) == len(base) * len(profiles)
        srcs = {it.trial.confidence_source for it in expanded}
        assert srcs == {f'synthetic:{p}' for p in profiles}
        # 같은 (scenario·baseline·fault·episode) cell 은 프로파일 무관 동일 seed.
        by_coords: dict = {}
        for it in expanded:
            t = it.trial
            key = (t.scenario_id, t.baseline_config.mode.value,
                   t.fault_scenario.name, t.episode_id)
            by_coords.setdefault(key, set()).add(t.seed)
        assert all(len(seeds) == 1 for seeds in by_coords.values())

    def test_plan_json_roundtrip_with_profile(self, fault_names: list[str]) -> None:
        """plan JSON 의 confidence_source 좌표 → build_trial_from_coords → 동일 trial."""
        profiles = ('c_constant_1', 'c_stall')
        obj = plan_to_json_obj(plan_run(self._config(profiles, fault_names[:1])))
        assert len(obj['trials']) == 2 * len(profiles)
        for jt in obj['trials']:
            assert jt['confidence_source'].startswith('synthetic:')
            assert jt['trial_id'].endswith(
                '__c-' + jt['confidence_source'][len('synthetic:'):]
            )
            rebuilt = build_trial_from_coords(
                scenario=jt['scenario'],
                baseline=jt['baseline'],
                fault=jt['fault'],
                episode=jt['episode'],
                confidence_source=jt['confidence_source'],
            )
            assert rebuilt.trial_id == jt['trial_id']
