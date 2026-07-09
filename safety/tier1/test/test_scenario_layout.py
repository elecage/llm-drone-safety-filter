"""tier1_filter.scenario_layout 단위 테스트.

PR #146 amendment (M-1) — tier1_b1.launch.py + tier1_b2.launch.py 측 *공통 module*
측 single source-of-truth. scenario lookup + r_min 잠금 + unknown scenario 측
RuntimeError + caller mutation 회피 (dict 복사 반환) 검증.
"""

from __future__ import annotations

import pytest

from tier1_filter.scenario_layout import (
    SCENARIO_USER_PARAMS,
    resolve_scenario_params,
)


# -------------------------------------------------------------------- SCENARIO_USER_PARAMS


class TestScenarioUserParams:
    def test_two_scenarios_locked(self) -> None:
        assert set(SCENARIO_USER_PARAMS.keys()) == {'livingroom', 'yard'}

    def test_livingroom_coords_v4_1_layout(self) -> None:
        """v4.1 layout (2026-05-30) 측 user local ENU 좌표 잠금 — 소파 동쪽 옆자리."""
        p = SCENARIO_USER_PARAMS['livingroom']
        assert p['user_local_x'] == -0.5
        assert p['user_local_y'] == 2.0
        assert p['user_local_z'] == 0.95

    def test_yard_coords_s8_layout(self) -> None:
        """S8 yard_base.sdf 측 user world (0, -3, 1.1) - drone spawn (0, -2, 0.15)
        = local (0, -1, 0.95) 잠금.
        """
        p = SCENARIO_USER_PARAMS['yard']
        assert p['user_local_x'] == 0.0
        assert p['user_local_y'] == -1.0
        assert p['user_local_z'] == 0.95

    def test_r_min_same_across_scenarios(self) -> None:
        """cmsm-proof §7.1 P1 측 r_min=0.9 m 측 모든 scenario 동일."""
        for params in SCENARIO_USER_PARAMS.values():
            assert params['r_min'] == 0.9

    def test_each_scenario_has_four_keys(self) -> None:
        for params in SCENARIO_USER_PARAMS.values():
            assert set(params.keys()) == {
                'user_local_x', 'user_local_y', 'user_local_z', 'r_min',
            }


# -------------------------------------------------------------------- resolve_scenario_params


class TestResolveScenarioParams:
    def test_livingroom_returns_correct_dict(self) -> None:
        params = resolve_scenario_params('livingroom')
        assert params == {
            'user_local_x': -0.5, 'user_local_y': 2.0, 'user_local_z': 0.95,
            'r_min': 0.9,
        }

    def test_yard_returns_correct_dict(self) -> None:
        params = resolve_scenario_params('yard')
        assert params == {
            'user_local_x': 0.0, 'user_local_y': -1.0, 'user_local_z': 0.95,
            'r_min': 0.9,
        }

    def test_unknown_scenario_raises(self) -> None:
        """typo 측 launch 측 fail-fast (silent default 회피)."""
        with pytest.raises(RuntimeError, match='unknown'):
            resolve_scenario_params('outdoor')

    def test_empty_scenario_raises(self) -> None:
        with pytest.raises(RuntimeError, match='unknown'):
            resolve_scenario_params('')

    def test_returns_copy_not_original(self) -> None:
        """caller 측 mutation 측 original lookup table 측 affect 안 함."""
        params = resolve_scenario_params('livingroom')
        params['user_local_x'] = 999.9
        # original 측 무변경 (v4.1 layout: x = -0.5 동일)
        assert SCENARIO_USER_PARAMS['livingroom']['user_local_x'] == -0.5
        # 새 lookup 측 *원본 값*
        params2 = resolve_scenario_params('livingroom')
        assert params2['user_local_x'] == -0.5
