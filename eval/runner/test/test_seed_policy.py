"""eval_runner.seed_policy 단위 테스트.

ROADMAP C25 closure — 5 차원 deterministic seed 측 재현성·distinct·범위 검증.
"""

from __future__ import annotations

import pytest

from eval_runner.seed_policy import derive_trial_seed


class TestDeterministic:
    def test_same_input_same_seed(self) -> None:
        """동일 5-tuple 측 동일 seed (재현성 핵심)."""
        s1 = derive_trial_seed('S5', 'b0', 'none', None, 0)
        s2 = derive_trial_seed('S5', 'b0', 'none', None, 0)
        assert s1 == s2

    def test_repeat_many(self) -> None:
        """100 회 호출 측 모두 동일 — purity 보장."""
        seeds = {
            derive_trial_seed('S6', 'b2', 'hallucination', 'gauss_low', 5)
            for _ in range(100)
        }
        assert len(seeds) == 1


class TestDistinct:
    def test_scenario_distinct(self) -> None:
        """scenario_id 변경 측 seed 변경."""
        s_s5 = derive_trial_seed('S5', 'b0', 'none', None, 0)
        s_s6 = derive_trial_seed('S6', 'b0', 'none', None, 0)
        s_s7 = derive_trial_seed('S7', 'b0', 'none', None, 0)
        s_s8 = derive_trial_seed('S8', 'b0', 'none', None, 0)
        assert len({s_s5, s_s6, s_s7, s_s8}) == 4

    def test_baseline_distinct(self) -> None:
        """baseline 변경 측 seed 변경."""
        seeds = {
            derive_trial_seed('S5', m, 'none', None, 0)
            for m in ('b0', 'b1a', 'b1b', 'b2', 'b3', 'b4')
        }
        assert len(seeds) == 6

    def test_fault_channel_distinct(self) -> None:
        """fault_channel 변경 측 seed 변경."""
        seeds = {
            derive_trial_seed('S5', 'b0', c, 'v', 0)
            for c in ('none', 'hallucination', 'adversarial',
                      'cognitive_lapse', 'attribute_mismatch')
        }
        assert len(seeds) == 5

    def test_fault_variant_distinct(self) -> None:
        """fault_variant 변경 측 seed 변경."""
        s1 = derive_trial_seed('S5', 'b0', 'hallucination', 'gauss_low', 0)
        s2 = derive_trial_seed('S5', 'b0', 'hallucination', 'gauss_med', 0)
        s3 = derive_trial_seed('S5', 'b0', 'hallucination', 'worst_geofence', 0)
        assert len({s1, s2, s3}) == 3

    def test_episode_distinct(self) -> None:
        """episode_id 변경 측 seed 변경 (10 episode 모두 distinct)."""
        seeds = {
            derive_trial_seed('S5', 'b0', 'none', None, e)
            for e in range(10)
        }
        assert len(seeds) == 10

    def test_none_variant_vs_none_string_distinct(self) -> None:
        """variant=None ↔ variant='none' 측 *구별* — separator (\\x00) 측
        boundary 명확화 측 결과.

        variant=None → 빈 문자열 처리 (\\x00이 양옆에 연속), variant='none' →
        실제 'none' string 4 글자 측 SHA-256 결과 distinct.
        """
        s_none = derive_trial_seed('S5', 'b0', 'none', None, 0)
        s_str = derive_trial_seed('S5', 'b0', 'none', 'none', 0)
        assert s_none != s_str


class TestRange:
    def test_uint32_range(self) -> None:
        """seed 측 uint32 범위 — [0, 2**32)."""
        for sid in ('S5', 'S6', 'S7', 'S8'):
            for mode in ('b0', 'b1a', 'b1b', 'b2', 'b3', 'b4'):
                for ep in range(10):
                    seed = derive_trial_seed(sid, mode, 'none', None, ep)
                    assert 0 <= seed < 2**32

    def test_full_grid_seeds_mostly_distinct(self) -> None:
        """ADR-0025 D3 default 격자 (amendment 19) 1200 trial 측 seed 측 *대다수* distinct —
        uint32 측 1200 sample 측 birthday paradox 측 collision 확률 ~0.017 %
        (1200² / (2 × 2**32) ≈ 1.67e-4). 1 회 collision 측 허용, 5 회 이상 측
        hash 정합성 의심.
        """
        scenarios = ('S5', 'S6', 'S7', 'S8')
        baselines = ('b0', 'b1a', 'b1b', 'b2', 'b3', 'b4')
        # ADR-0025 D5 #5a 5 fault scenarios 측 (channel, variant) 4-tuple 정합.
        faults = (
            ('none', None),
            ('hallucination', 'position_gauss_low'),
            ('adversarial', 'prompt_injection_geofence'),
            ('cognitive_lapse', 'self_correction'),
            ('attribute_mismatch', 'label_low'),
        )
        seeds = []
        for sid in scenarios:
            for mode in baselines:
                for channel, variant in faults:
                    for ep in range(10):
                        seeds.append(
                            derive_trial_seed(sid, mode, channel, variant, ep)
                        )
        assert len(seeds) == 1200
        # 최소 1195 distinct (collision 5 회 이하 허용).
        assert len(set(seeds)) >= 1195, (
            f'1200 trial 측 seed collision {1200 - len(set(seeds))} 회 — '
            f'hash 정합성 의심 (expected ~ 0 collision under SHA-256)'
        )


class TestValidation:
    def test_negative_episode_id(self) -> None:
        with pytest.raises(ValueError, match='episode_id'):
            derive_trial_seed('S5', 'b0', 'none', None, -1)

    def test_episode_id_type(self) -> None:
        with pytest.raises(TypeError, match='episode_id'):
            derive_trial_seed('S5', 'b0', 'none', None, '0')  # type: ignore[arg-type]

    def test_episode_id_bool(self) -> None:
        with pytest.raises(TypeError, match='episode_id'):
            derive_trial_seed('S5', 'b0', 'none', None, True)  # type: ignore[arg-type]


class TestKnownVector:
    """SHA-256 측 고정 출력 — 본 hash 함수 측 stability lock.

    본 vector 변경 측 *seed shift* — 격자 1000 trial 모두 seed 변동 → paper §C
    재현성 깨짐. 본 test 측 fail 시 함부로 update 금지 (seed policy 변경 측
    별 ADR 필요).

    본 vector 측 *hardcoded literal integer* — derive_trial_seed 내부 logic 변경
    (SHA-256 → MD5 / separator '\\x00' → ',') 측 *test 측 fail*. PR #121 self-review
    C-1 정정 — 이전 implementation 측 runtime hashlib 재계산 측 stability lock
    의도 정반대 (둘 다 변경되므로 test 통과). 본 정정 측 honesty 측 critical.
    """

    def test_canonical_vector_s5_b0_none(self) -> None:
        """vector 1 — ('S5', 'b0', 'none', None, 0) → 3847751998.

        Computed: SHA-256('S5\\x00b0\\x00none\\x00\\x000'.encode())[:4] big-endian.
        """
        assert derive_trial_seed('S5', 'b0', 'none', None, 0) == 3847751998

    def test_canonical_vector_s8_b4_attribute_mismatch(self) -> None:
        """vector 2 — ('S8', 'b4', 'attribute_mismatch', 'label_low', 9) → 1749053183.

        Computed: SHA-256('S8\\x00b4\\x00attribute_mismatch\\x00label_low\\x009'
        .encode())[:4] big-endian. 5 차원 모두 non-default 측 vector — 격자 corner
        측 stability 잠금.
        """
        assert (
            derive_trial_seed('S8', 'b4', 'attribute_mismatch', 'label_low', 9)
            == 1749053183
        )
