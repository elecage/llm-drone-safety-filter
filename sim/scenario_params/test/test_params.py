"""scenario_params.params 단위 테스트.

단일 진실 소스 잠금:
  - world 좌표 + spawn 오프셋 + local ENU 좌표 + r_min 잠금
  - user_marker_params() / tier1_local_params() 반환 형태 검증
  - local = world − spawn 정합성 (abs_tol=1e-9 m)
  - unknown scenario 측 RuntimeError
  - 반환 dict 측 caller mutation 격리
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# host venv pytest 측 패키지 경로 보장
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenario_params.params import (
    GAMMA,
    U_MAX,
    VALID_SCENARIO_IDS,
    VALID_SCENARIOS,
    _R_MAX_BY_SCENARIO,
    _SCENARIOS,
    cbf_availability_margin,
    is_cbf_available,
    scenario_ovd_vocab,
    scenario_target_class,
    tier1_cbf_params,
    tier1_local_params,
    user_marker_params,
)


# ------------------------------------------------------------------ VALID_SCENARIOS


class TestValidScenarios:
    def test_two_scenarios_locked(self) -> None:
        assert VALID_SCENARIOS == {'livingroom', 'yard'}


# ------------------------------------------------------------------ user_marker_params


class TestUserMarkerParams:
    def test_livingroom_keys(self) -> None:
        assert set(user_marker_params('livingroom').keys()) == {
            'user_x', 'user_y', 'user_z', 'r_min',
        }

    def test_yard_keys(self) -> None:
        assert set(user_marker_params('yard').keys()) == {
            'user_x', 'user_y', 'user_z', 'r_min',
        }

    def test_livingroom_world_coords(self) -> None:
        """v4.1 layout (2026-05-30) 측 월드 좌표 잠금 — 소파 동쪽 옆자리."""
        p = user_marker_params('livingroom')
        assert p['user_x'] == 0.0
        assert p['user_y'] == 1.5
        assert p['user_z'] == 1.1

    def test_yard_world_coords(self) -> None:
        """S8 yard_base.sdf 측 사용자 머리 월드 좌표 잠금."""
        p = user_marker_params('yard')
        assert p['user_x'] == 0.0
        assert p['user_y'] == -3.0
        assert p['user_z'] == 1.1

    def test_r_min_uniform(self) -> None:
        """cmsm-proof §7.1 P1 측 r_min=0.9 m 모든 scenario 동일."""
        for s in VALID_SCENARIOS:
            assert user_marker_params(s)['r_min'] == 0.9

    def test_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError, match='unknown'):
            user_marker_params('outdoor')

    def test_returns_copy(self) -> None:
        p = user_marker_params('livingroom')
        p['user_x'] = 999.0
        assert user_marker_params('livingroom')['user_x'] == 0.0


# ------------------------------------------------------------------ tier1_local_params


class TestTier1LocalParams:
    def test_livingroom_keys(self) -> None:
        assert set(tier1_local_params('livingroom').keys()) == {
            'user_local_x', 'user_local_y', 'user_local_z', 'r_min',
        }

    def test_yard_keys(self) -> None:
        assert set(tier1_local_params('yard').keys()) == {
            'user_local_x', 'user_local_y', 'user_local_z', 'r_min',
        }

    def test_livingroom_local_coords(self) -> None:
        """v4.1 layout (2026-05-30) 측 local ENU 좌표 잠금 — world(0,1.5,1.1) − spawn(0.5,-0.5,0.15)."""
        p = tier1_local_params('livingroom')
        assert p['user_local_x'] == -0.5
        assert p['user_local_y'] == 2.0
        assert p['user_local_z'] == 0.95

    def test_yard_local_coords(self) -> None:
        """S8 yard layout 측 local ENU 좌표 잠금."""
        p = tier1_local_params('yard')
        assert p['user_local_x'] == 0.0
        assert p['user_local_y'] == -1.0
        assert p['user_local_z'] == 0.95

    def test_r_min_uniform(self) -> None:
        for s in VALID_SCENARIOS:
            assert tier1_local_params(s)['r_min'] == 0.9

    def test_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError, match='unknown'):
            tier1_local_params('outdoor')

    def test_returns_copy(self) -> None:
        p = tier1_local_params('yard')
        p['user_local_y'] = 999.0
        assert tier1_local_params('yard')['user_local_y'] == -1.0


# ------------------------------------------------------------------ 정합성 (local = world − spawn)


class TestLocalWorldSpawnConsistency:
    def test_local_equals_world_minus_spawn(self) -> None:
        """local 명시값 측 world − spawn 연산과 abs_tol=1e-9 m 이내 정합."""
        for name, s in _SCENARIOS.items():
            wx, wy, wz = s['world']
            sx, sy, sz = s['spawn']
            lx, ly, lz = s['local']
            assert math.isclose(lx, wx - sx, abs_tol=1e-9), \
                f"{name}: local_x={lx} vs world_x-spawn_x={wx - sx}"
            assert math.isclose(ly, wy - sy, abs_tol=1e-9), \
                f"{name}: local_y={ly} vs world_y-spawn_y={wy - sy}"
            assert math.isclose(lz, wz - sz, abs_tol=1e-9), \
                f"{name}: local_z={lz} vs world_z-spawn_z={wz - sz}"


# ------------------------------------------------------------------ tier1_cbf_params (ADR-0023)


class TestValidScenarioIds:
    def test_scenario_ids_locked(self) -> None:
        # ADR-0039 D2: 거실 S5/S6 만 (S7 폐기·S8 paper-2 이관).
        assert VALID_SCENARIO_IDS == {'S5', 'S6'}


class TestTier1CbfParams:
    """ADR-0023 시나리오별 r_max 잠금 + dot_c_max 파생."""

    def test_keys(self) -> None:
        assert set(tier1_cbf_params('S5').keys()) == {
            'user_local_x', 'user_local_y', 'user_local_z',
            'r_min', 'r_max', 'gamma', 'u_max', 'dot_c_max',
        }

    @pytest.mark.parametrize('sid, r_max', [
        ('S5', 1.80), ('S6', 1.80),
    ])
    def test_r_max_locked(self, sid: str, r_max: float) -> None:
        """ADR-0023 + 세션 49 amendment — S5/S6=1.80(소파 작업 도달성).
        종전 2.00(도크 바인딩)에서 소파 viewpoint(1.93) 도달 위해 1.80 인하."""
        assert tier1_cbf_params(sid)['r_max'] == r_max

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_r_min_uniform_09(self, sid: str) -> None:
        assert tier1_cbf_params(sid)['r_min'] == 0.9

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_gamma_u_max_invariant(self, sid: str) -> None:
        """cmsm-proof §7.1 P3/P4 — gamma·u_max 시나리오 무관."""
        p = tier1_cbf_params(sid)
        assert p['gamma'] == GAMMA == 4.0
        assert p['u_max'] == U_MAX == 0.5

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_dot_c_max_derived(self, sid: str) -> None:
        """dot_c_max = u_max/(r_max − r_min) (cmsm-proof §6 가용성, C11 해소)."""
        p = tier1_cbf_params(sid)
        assert math.isclose(
            p['dot_c_max'], p['u_max'] / (p['r_max'] - p['r_min']), rel_tol=1e-12
        )

    def test_dot_c_max_values(self) -> None:
        assert math.isclose(tier1_cbf_params('S5')['dot_c_max'], 0.5 / 0.9, rel_tol=1e-12)
        assert math.isclose(tier1_cbf_params('S6')['dot_c_max'], 0.5 / 0.9, rel_tol=1e-12)

    def test_livingroom_ids_share_user_coords(self) -> None:
        """S5/S6 모두 livingroom local 좌표 (r_max 동일 1.80)."""
        for sid in ('S5', 'S6'):
            p = tier1_cbf_params(sid)
            assert (p['user_local_x'], p['user_local_y'], p['user_local_z']) == (-0.5, 2.0, 0.95)

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_r_max_strictly_above_r_min(self, sid: str) -> None:
        p = tier1_cbf_params(sid)
        assert p['r_max'] > p['r_min']

    def test_r_max_table_matches_source(self) -> None:
        assert _R_MAX_BY_SCENARIO == {'S5': 1.80, 'S6': 1.80}

    def test_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError, match='unknown'):
            tier1_cbf_params('S3')

    def test_location_unknown_raises(self) -> None:
        """location 키('livingroom')는 scenario_id 가 아님 — raise."""
        with pytest.raises(RuntimeError, match='unknown'):
            tier1_cbf_params('livingroom')

    def test_returns_copy(self) -> None:
        p = tier1_cbf_params('S5')
        p['r_max'] = 999.0
        assert tier1_cbf_params('S5')['r_max'] == 1.80


# ------------------------------------------------------------------ 가용성 (T2-4)


class TestCbfAvailability:
    """cmsm-proof §6 가용성 (r_max − r_min)·dot_c_max ≤ u_max."""

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_derived_margin_zero(self, sid: str) -> None:
        """파생 dot_c_max 는 가용성 등호 → margin ≈ 0."""
        p = tier1_cbf_params(sid)
        margin = cbf_availability_margin(
            p['r_min'], p['r_max'], p['u_max'], p['dot_c_max']
        )
        assert math.isclose(margin, 0.0, abs_tol=1e-9)

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_derived_available(self, sid: str) -> None:
        p = tier1_cbf_params(sid)
        assert is_cbf_available(p['r_min'], p['r_max'], p['u_max'], p['dot_c_max'])

    def test_too_fast_not_available(self) -> None:
        """dot_c_max 가 derive 값보다 크면 가용성 위반."""
        # r_min=0.9, r_max=2.0 → derive 0.4545. 1.0 은 (2.0−0.9)·1.0=1.1 > 0.5.
        assert not is_cbf_available(0.9, 2.0, 0.5, 1.0)
        assert cbf_availability_margin(0.9, 2.0, 0.5, 1.0) < 0

    def test_slower_available(self) -> None:
        """derive 값보다 느린(작은) dot_c_max 는 가용 (margin > 0)."""
        assert is_cbf_available(0.9, 2.0, 0.5, 0.3)
        assert cbf_availability_margin(0.9, 2.0, 0.5, 0.3) > 0


# ------------------------------------------------------------ scenario_target_class


class TestScenarioTargetClass:
    """SR post-hoc 평가기(ADR-0032 D2)가 쓰는 scenario → OVD 클래스 매핑."""

    def test_mapping_matches_utterance_referents(self) -> None:
        # _SCENARIO_UTTERANCE: S5=머그컵(cup), S6=소파(sofa).
        assert scenario_target_class('S5') == 'cup'
        assert scenario_target_class('S6') == 'sofa'

    def test_unknown_scenario_raises(self) -> None:
        with pytest.raises(RuntimeError):
            scenario_target_class('S99')


# ------------------------------------------------------------- scenario_ovd_vocab


class TestScenarioOvdVocab:
    """OVD 정적 어휘가 발화 referent 클래스를 *반드시* 포함 — 세션 53 B4 게이트
    e2e 가 적발한 결함(거실 referent 'sofa'·마당 'person' 이 어휘 밖이라 grounding
    영구 실패 → c=0 → 게이트 전부 reject)을 직접 차단하는 불변식."""

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_referent_class_in_per_scenario_vocab(self, sid: str) -> None:
        assert scenario_target_class(sid) in scenario_ovd_vocab(sid)

    @pytest.mark.parametrize('sid', ['S5', 'S6'])
    def test_referent_class_in_union_vocab(self, sid: str) -> None:
        """영속 OVD(합집합 어휘)도 전 시나리오 referent 를 덮어야 한다."""
        from scenario_params.scene import ovd_vocabulary_all
        assert scenario_target_class(sid) in ovd_vocabulary_all()

    def test_livingroom_scenarios_vocab(self) -> None:
        # S5 머그컵 통합(ADR-0035)으로 거실 어휘에 'cup' 추가. S6 도 동일 거실
        # 월드를 공유하므로 같은 어휘(chair·table 은 무관 후보로 무해).
        for sid in ('S5', 'S6'):
            assert scenario_ovd_vocab(sid) == ['chair', 'cup', 'sofa', 'table']

    def test_unknown_scenario_raises(self) -> None:
        with pytest.raises(RuntimeError):
            scenario_ovd_vocab('S99')
