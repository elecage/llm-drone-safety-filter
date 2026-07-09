"""schemas.py 단위 테스트 — FaultVariant + FaultContext."""

from __future__ import annotations

import pytest

from eval_faults.schemas import (
    FaultContext,
    FaultVariant,
    FREQUENCY_VARIANTS,
    POSITIONAL_VARIANTS,
    REFERENTIAL_VARIANTS,
    SKILL_AGNOSTIC_POSITIONAL_VARIANTS,
)


class TestFaultVariant:
    def test_ten_variants_locked(self):
        """amendment 16 하이브리드 8 + amendment 20 Track B 1 + C38 Φ_1 geofence 1 = 10."""
        names = {v.value for v in FaultVariant}
        assert names == {
            'position_noise_gauss_low',
            'position_noise_gauss_med',
            'position_noise_worst_geofence',
            'position_worst_user_direct',
            'position_geofence_out_direct',
            'target_swap_random',
            'target_swap_nearest',
            'target_swap_dangerous',
            'target_swap_natural',
            'target_swap_amplified',
        }

    def test_positional_set_5(self):
        """positional 3 + 사용자 지향 1 (worst_user) + Φ_1 geofence 1 (C38) = 5."""
        assert len(POSITIONAL_VARIANTS) == 5
        for v in POSITIONAL_VARIANTS:
            assert 'position' in v.value

    def test_skill_agnostic_positional_subset(self):
        """스킬 무관 변형 = worst_user_direct(하한) + geofence_out_direct(Φ_1, C38)."""
        assert SKILL_AGNOSTIC_POSITIONAL_VARIANTS == {
            FaultVariant.POSITION_WORST_USER_DIRECT,
            FaultVariant.POSITION_GEOFENCE_OUT_DIRECT,
        }
        assert SKILL_AGNOSTIC_POSITIONAL_VARIANTS <= POSITIONAL_VARIANTS

    def test_referential_set_5(self):
        """amendment 16 — 정책 3 (random/nearest/dangerous) + 빈도 2 (natural/amplified)."""
        assert len(REFERENTIAL_VARIANTS) == 5
        for v in REFERENTIAL_VARIANTS:
            assert 'target_swap' in v.value

    def test_frequency_set_2(self):
        """빈도 variant 2 종 — referential 의 부분집합."""
        assert FREQUENCY_VARIANTS == {
            FaultVariant.TARGET_SWAP_NATURAL,
            FaultVariant.TARGET_SWAP_AMPLIFIED,
        }
        assert FREQUENCY_VARIANTS <= REFERENTIAL_VARIANTS

    def test_positional_referential_disjoint(self):
        assert POSITIONAL_VARIANTS.isdisjoint(REFERENTIAL_VARIANTS)
        assert POSITIONAL_VARIANTS | REFERENTIAL_VARIANTS == set(FaultVariant)


class TestFaultContext:
    def _minimal(self, **overrides) -> FaultContext:
        kwargs = dict(
            known_objects={'a': (0.0, 0.0, 0.0), 'b': (1.0, 0.0, 0.0)},
            user_position=(0.0, -1.0, 1.1),
        )
        kwargs.update(overrides)
        return FaultContext(**kwargs)

    def test_minimal_defaults(self):
        ctx = self._minimal()
        # ADR-0026 D4 / ADR-0025 1차 default
        assert ctx.r_min == 0.7
        assert ctx.sigma_llm_nat_cm == 10.0
        # amendment 16 — referential calibration + positional 절대 cm 1차 default
        assert ctx.referent_swap_rate == 0.05
        assert ctx.position_noise_low_cm == 5.0
        assert ctx.position_noise_med_cm == 50.0
        # 거실 v3 1차 default geofence
        assert ctx.geofence == (-3.0, 3.0, -2.0, 2.0, 0.0, 2.4)

    def test_referent_swap_rate_range(self):
        with pytest.raises(ValueError, match='referent_swap_rate'):
            self._minimal(referent_swap_rate=1.5)
        with pytest.raises(ValueError, match='referent_swap_rate'):
            self._minimal(referent_swap_rate=-0.1)

    def test_position_noise_cm_nonneg(self):
        with pytest.raises(ValueError, match='position_noise_low_cm'):
            self._minimal(position_noise_low_cm=-1.0)
        with pytest.raises(ValueError, match='position_noise_med_cm'):
            self._minimal(position_noise_med_cm=-1.0)

    def test_known_objects_must_be_dict(self):
        with pytest.raises(TypeError, match='dict'):
            FaultContext(
                known_objects=[('a', (0, 0, 0))],  # list, not dict
                user_position=(0.0, 0.0, 0.0),
            )

    def test_user_position_must_be_3tuple(self):
        with pytest.raises(ValueError, match='user_position'):
            FaultContext(
                known_objects={'a': (0, 0, 0)},
                user_position=(0.0, 0.0),  # 2-tuple
            )

    def test_r_min_positive(self):
        with pytest.raises(ValueError, match='r_min'):
            self._minimal(r_min=0.0)
        with pytest.raises(ValueError, match='r_min'):
            self._minimal(r_min=-0.1)

    def test_sigma_nonnegative(self):
        # 0 OK (calibration 측 σ_LLM,nat 가 0 일 수도 — modern LLM honest)
        ctx = self._minimal(sigma_llm_nat_cm=0.0)
        assert ctx.sigma_llm_nat_cm == 0.0
        with pytest.raises(ValueError, match='sigma_llm_nat_cm'):
            self._minimal(sigma_llm_nat_cm=-1.0)

    def test_geofence_must_be_6tuple(self):
        with pytest.raises(ValueError, match='geofence'):
            self._minimal(geofence=(-3.0, 3.0, -2.0, 2.0))  # 4-tuple

    def test_geofence_intervals_valid(self):
        # x_min >= x_max 거부
        with pytest.raises(ValueError, match='geofence'):
            self._minimal(geofence=(3.0, 3.0, -2.0, 2.0, 0.0, 2.4))
        with pytest.raises(ValueError, match='geofence'):
            self._minimal(geofence=(-3.0, 3.0, 2.0, -2.0, 0.0, 2.4))
        with pytest.raises(ValueError, match='geofence'):
            self._minimal(geofence=(-3.0, 3.0, -2.0, 2.0, 2.0, 0.0))
