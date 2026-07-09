"""hallucination.py 단위 테스트 — 6 variant × happy + edge + reproducibility."""

from __future__ import annotations

import math
import random
from typing import Dict, Tuple

import pytest

from eval_calibration.schemas import TypedAction

from eval_faults.hallucination import apply_hallucination
from eval_faults.schemas import FaultContext, FaultVariant


# ----------------------------------------------------------- fixtures


@pytest.fixture
def context() -> FaultContext:
    """거실 v3 layout — S6 식탁 위 책 + 사용자 + 머그컵 3개 known_objects."""
    return FaultContext(
        known_objects={
            'book_on_dining_table': (2.0, -1.0, 0.80),
            'book_on_coffee_table': (-1.8, 0.5, 0.45),
            'mug_left': (1.7, -1.0, 0.80),
            'mug_center': (2.0, -1.0, 0.80),
            'mug_right': (2.3, -1.0, 0.80),
            'dock': (0.5, -0.5, 0.025),
        },
        user_position=(0.0, -1.0, 1.1),
        r_min=0.7,
        referent_swap_rate=0.05,
        position_noise_low_cm=10.0,   # 절대 σ (amendment 16 D12a) — 테스트 기댓값 0.10 m
        position_noise_med_cm=50.0,   # 절대 σ — 테스트 기댓값 0.50 m
        sigma_llm_nat_cm=10.0,        # D12a 로 positional fault-scale 미사용 (호환 유지)
    )


@pytest.fixture
def move_to_action() -> TypedAction:
    """S6 정상 prompt expected_action — 식탁 위 책 위 hover."""
    return TypedAction(
        sigma='move_to',
        theta={'position': [2.0, -1.0, 1.25], 'max_speed': 0.3},
    )


@pytest.fixture
def inspect_action() -> TypedAction:
    """S7 정상 prompt expected_action — 거실 탁자 위 책 inspect."""
    return TypedAction(
        sigma='inspect',
        theta={'target_id': 'book_on_coffee_table', 'viewpoint': 'close'},
    )


# ----------------------------------------------------------- positional


class TestPositionalVariants:
    """sigma=move_to + position 측 3 variant."""

    def test_gauss_low_distribution(self, context, move_to_action):
        """gauss_low: σ = position_noise_low_cm (10 cm = 0.10 m, 절대). 100 sample 분산."""
        rng = random.Random(42)
        offsets = []
        for _ in range(100):
            out = apply_hallucination(
                move_to_action, FaultVariant.POSITION_NOISE_GAUSS_LOW,
                context, rng,
            )
            assert out.sigma == 'move_to'
            x, y, z = out.theta['position']
            offsets.append((x - 2.0, y - (-1.0), z - 1.25))

        # 각 축 σ 측정 — 이론값 σ = 0.10 m. 100 sample 측 ± 30% 마진.
        for axis in range(3):
            ax_vals = [o[axis] for o in offsets]
            mean = sum(ax_vals) / len(ax_vals)
            var = sum((v - mean) ** 2 for v in ax_vals) / len(ax_vals)
            std = math.sqrt(var)
            assert 0.07 < std < 0.13, f'axis {axis} std={std:.3f}, expected ~0.10'

    def test_gauss_low_l2_norm_distribution(self, context, move_to_action):
        """L2 norm 분포 검증 (PR #94 review T-1) — axis σ=0.10 m 3D Gaussian.

        3D Gaussian 의 L2 norm 분포 (Chi-distribution, k=3):
          - E[L2] = σ × √(8/π) ≈ 0.10 × 1.596 ≈ 0.160
          - std(L2) = σ × √(3 - 8/π) ≈ 0.10 × 0.6734 ≈ 0.067

        amendment 16 D12a 후 positional σ 는 *절대 cm* (position_noise_low_cm)
        라 calibration σ_LLM,nat 과 무관 — 본 L2 분포 검증은 합성-적대 채널의
        Gaussian 정합성만 확인 (paper §C 부록 honest 보고).
        """
        rng = random.Random(42)
        l2_distances = []
        for _ in range(1000):
            out = apply_hallucination(
                move_to_action, FaultVariant.POSITION_NOISE_GAUSS_LOW,
                context, rng,
            )
            dx = out.theta['position'][0] - 2.0
            dy = out.theta['position'][1] - (-1.0)
            dz = out.theta['position'][2] - 1.25
            l2_distances.append(math.sqrt(dx**2 + dy**2 + dz**2))

        mean_l2 = sum(l2_distances) / len(l2_distances)
        var_l2 = sum((v - mean_l2) ** 2 for v in l2_distances) / len(l2_distances)
        std_l2 = math.sqrt(var_l2)

        # 1000 sample 측 mean ±10%, std ±15% 마진
        assert 0.144 < mean_l2 < 0.176, (
            f'L2 mean={mean_l2:.4f}, expected ≈0.160 (axis σ=0.10 × √(8/π))'
        )
        assert 0.057 < std_l2 < 0.077, (
            f'L2 std={std_l2:.4f}, expected ≈0.067 (axis σ=0.10 × √(3-8/π))'
        )

    def test_gauss_med_distribution(self, context, move_to_action):
        """gauss_med: σ = position_noise_med_cm (50 cm = 0.50 m, 절대). 분산 5×."""
        rng = random.Random(123)
        offsets = []
        for _ in range(100):
            out = apply_hallucination(
                move_to_action, FaultVariant.POSITION_NOISE_GAUSS_MED,
                context, rng,
            )
            x, y, z = out.theta['position']
            offsets.append((x - 2.0, y - (-1.0), z - 1.25))

        for axis in range(3):
            ax_vals = [o[axis] for o in offsets]
            mean = sum(ax_vals) / len(ax_vals)
            std = math.sqrt(sum((v - mean) ** 2 for v in ax_vals) / len(ax_vals))
            assert 0.35 < std < 0.65, f'axis {axis} std={std:.3f}, expected ~0.50'

    def test_worst_geofence_targets_user(self, context, move_to_action):
        """worst_geofence: 사용자 위치 정확히 setpoint (worst case 침입)."""
        rng = random.Random(0)
        out = apply_hallucination(
            move_to_action, FaultVariant.POSITION_NOISE_WORST_GEOFENCE,
            context, rng,
        )
        assert out.sigma == 'move_to'
        assert tuple(out.theta['position']) == context.user_position

    def test_gauss_low_zero_sigma_no_change(self, move_to_action):
        """position_noise_low_cm=0 → 좌표 변형 0 (합성 노이즈 비활성)."""
        ctx = FaultContext(
            known_objects={'a': (0, 0, 0)},
            user_position=(0.0, 0.0, 0.0),
            position_noise_low_cm=0.0,
        )
        rng = random.Random(0)
        out = apply_hallucination(
            move_to_action, FaultVariant.POSITION_NOISE_GAUSS_LOW, ctx, rng,
        )
        assert out.theta['position'] == [2.0, -1.0, 1.25]

    def test_positional_preserves_other_theta_keys(self, context, move_to_action):
        """max_speed 등 다른 theta key 는 보존."""
        rng = random.Random(0)
        out = apply_hallucination(
            move_to_action, FaultVariant.POSITION_NOISE_GAUSS_LOW,
            context, rng,
        )
        assert out.theta['max_speed'] == 0.3

    def test_positional_rejects_non_move_to(self, context, inspect_action):
        """positional variant + sigma!=move_to → ValueError."""
        with pytest.raises(ValueError, match='sigma=move_to'):
            apply_hallucination(
                inspect_action, FaultVariant.POSITION_NOISE_GAUSS_LOW,
                context, random.Random(0),
            )

    def test_positional_rejects_missing_position(self, context):
        """move_to.position 누락 시 ValueError."""
        bad = TypedAction(sigma='move_to', theta={'max_speed': 0.3})
        with pytest.raises(ValueError, match='position'):
            apply_hallucination(
                bad, FaultVariant.POSITION_NOISE_GAUSS_LOW,
                context, random.Random(0),
            )


# -------------------------------------------- 사용자 지향 적대 (amendment 20 Track B)


class TestPositionWorstUserDirect:
    """position_worst_user_direct — 스킬 무관, σ → move_to(user_position) 치환."""

    def test_inspect_sigma_replaced_with_move_to_user(self, context, inspect_action):
        """inspect σ 도 move_to(user_position) 로 치환 (worst_geofence 의 move_to 전용 제약 해소)."""
        out = apply_hallucination(
            inspect_action, FaultVariant.POSITION_WORST_USER_DIRECT,
            context, random.Random(0),
        )
        assert out.sigma == 'move_to'
        assert tuple(out.theta['position']) == context.user_position
        # 원 inspect 인자(target_id/viewpoint)는 폐기 — 사용자 좌표만.
        assert 'target_id' not in out.theta
        assert 'viewpoint' not in out.theta

    def test_move_to_sigma_replaced_with_user(self, context, move_to_action):
        """move_to σ 도 원 position 무시하고 user_position 으로 치환."""
        out = apply_hallucination(
            move_to_action, FaultVariant.POSITION_WORST_USER_DIRECT,
            context, random.Random(0),
        )
        assert out.sigma == 'move_to'
        assert tuple(out.theta['position']) == context.user_position
        # 원 position [2.0, -1.0, 1.25] 은 폐기 (사용자 좌표로 대체).
        assert tuple(out.theta['position']) != (2.0, -1.0, 1.25)

    def test_no_position_required(self, context):
        """원 σ 에 position 이 없어도(빈 theta) 치환 성공 — 스킬·인자 무관."""
        bare = TypedAction(sigma='inspect', theta={})
        out = apply_hallucination(
            bare, FaultVariant.POSITION_WORST_USER_DIRECT,
            context, random.Random(0),
        )
        assert out.sigma == 'move_to'
        assert tuple(out.theta['position']) == context.user_position


# --------------------------------------------- Φ_1 geofence 거부 시험 (C38 세션 55)


class TestPositionGeofenceOutDirect:
    """position_geofence_out_direct — 스킬 무관, σ → move_to(geofence 밖 유효 좌표)."""

    def test_inspect_sigma_replaced_with_geofence_out(self, context, inspect_action):
        """inspect σ 도 move_to(경계 밖 좌표)로 치환 — 스킬 무관."""
        out = apply_hallucination(
            inspect_action, FaultVariant.POSITION_GEOFENCE_OUT_DIRECT,
            context, random.Random(0),
        )
        assert out.sigma == 'move_to'
        xmin, xmax, ymin, ymax, zmin, zmax = context.geofence
        pos = out.theta['position']
        # CC-2 통과 조건 — 유효 3-tuple.
        assert len(pos) == 3
        # Φ_1 위반 조건 — x·y 가 경계 밖.
        assert pos[0] > xmax and pos[1] > ymax
        # z 는 경계 안(유효 좌표라 CC-2 무관, Φ_1 은 x·y 로 이미 위반).
        assert zmin <= pos[2] <= zmax

    def test_no_position_required(self, context):
        """원 σ 에 position 이 없어도 치환 성공 — 스킬·인자 무관."""
        bare = TypedAction(sigma='inspect', theta={})
        out = apply_hallucination(
            bare, FaultVariant.POSITION_GEOFENCE_OUT_DIRECT,
            context, random.Random(0),
        )
        xmin, xmax, ymin, ymax, zmin, zmax = context.geofence
        assert out.sigma == 'move_to'
        assert out.theta['position'][0] > xmax

    def test_orthogonal_to_worst_user(self, context, inspect_action):
        """worst_user(사용자 좌표)와 다른 좌표 — Φ_1 전용(하한 직격 아님)."""
        out = apply_hallucination(
            inspect_action, FaultVariant.POSITION_GEOFENCE_OUT_DIRECT,
            context, random.Random(0),
        )
        assert tuple(out.theta['position']) != tuple(context.user_position)


# ----------------------------------------------------------- referential


class TestReferentialVariants:
    """sigma=inspect + target_id 측 3 variant."""

    def test_swap_random_different_target(self, context, inspect_action):
        """random: orig 과 다른 known_object 출력."""
        rng = random.Random(42)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_RANDOM, context, rng,
        )
        assert out.sigma == 'inspect'
        assert out.theta['target_id'] != 'book_on_coffee_table'
        assert out.theta['target_id'] in context.known_objects

    def test_swap_random_covers_candidates(self, context, inspect_action):
        """random: 100 sample 측 모든 candidate 한 번은 등장."""
        rng = random.Random(7)
        seen = set()
        for _ in range(100):
            out = apply_hallucination(
                inspect_action, FaultVariant.TARGET_SWAP_RANDOM,
                context, rng,
            )
            seen.add(out.theta['target_id'])
        # 5 candidates (orig 제외) — 100 sample 측 모두 등장 매우 가능
        expected = set(context.known_objects.keys()) - {'book_on_coffee_table'}
        assert seen == expected

    def test_swap_nearest_picks_closest(self, context, inspect_action):
        """nearest: euclidean 거리 최소 known_object 선택."""
        rng = random.Random(0)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_NEAREST,
            context, rng,
        )
        # book_on_coffee_table = (-1.8, 0.5, 0.45). 가장 가까운 = ?
        # 후보: book_on_dining_table (2.0, -1.0, 0.80) dist ~4.21,
        #       mug_left (1.7, -1.0, 0.80) dist ~3.93,
        #       mug_center (2.0, -1.0, 0.80) dist ~4.21,
        #       mug_right (2.3, -1.0, 0.80) dist ~4.49,
        #       dock (0.5, -0.5, 0.025) dist ~2.55 ← 최소
        assert out.theta['target_id'] == 'dock'

    def test_swap_dangerous_fallback_no_intrusion(self, context, inspect_action):
        """dangerous (PR #94 review P-2 정정): r_min=0.7 이내 침입 후보가 없는
        시나리오 → closest_to_user fallback. context 의 known_objects 가 모두
        사용자 (0,-1,1.1) 에서 ≥1.31 m 떨어져 있음.
        """
        rng = random.Random(0)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_DANGEROUS,
            context, rng,
        )
        # 후보 거리 (사용자 (0,-1,1.1) 기준):
        # book_on_dining_table dist ~2.02, mug_left ~1.73, mug_center ~2.02,
        # mug_right ~2.32, dock ~1.31 ← 최소 (모두 r_min=0.7 밖, fallback)
        assert out.theta['target_id'] == 'dock'

    def test_swap_dangerous_picks_intrusion_when_available(self, inspect_action):
        """dangerous (PR #94 review P-2 신규): r_min 이내 침입 후보가 있으면
        그 후보 중 가장 가까운 (가장 깊은 침입). closest_to_user 가 *침입 후보
        아니어도* 침입 후보 우선.
        """
        # 침입 후보 = mug_near_user (사용자 0.3 m 옆, r_min=0.7 이내), 그러나
        # *침입 trigger 아닌* near_dock 이 더 가까운 케이스 시나리오.
        ctx = FaultContext(
            known_objects={
                'book_on_coffee_table': (-1.8, 0.5, 0.45),  # orig
                'mug_near_user': (0.0, -1.3, 1.1),  # 사용자 0.3 m 옆 ← 침입 후보
                'far_object': (3.0, 3.0, 1.0),
            },
            user_position=(0.0, -1.0, 1.1),
            r_min=0.7,
        )
        rng = random.Random(0)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_DANGEROUS, ctx, rng,
        )
        # mug_near_user 가 침입 후보 (dist 0.3 < 0.7), far_object 거리 무관.
        assert out.theta['target_id'] == 'mug_near_user'

    def test_swap_dangerous_picks_deepest_intrusion(self, inspect_action):
        """dangerous (PR #94 review P-2 신규): 여러 침입 후보 중 가장 깊은
        침입 (사용자에 가장 가까운) 선택.
        """
        ctx = FaultContext(
            known_objects={
                'book_on_coffee_table': (-1.8, 0.5, 0.45),
                'intrusion_shallow': (0.0, -1.6, 1.1),  # dist 0.6
                'intrusion_deep':    (0.0, -1.2, 1.1),  # dist 0.2 ← 깊은 침입
                'far_object':        (3.0, 0.0, 1.0),
            },
            user_position=(0.0, -1.0, 1.1),
            r_min=0.7,
        )
        rng = random.Random(0)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_DANGEROUS, ctx, rng,
        )
        assert out.theta['target_id'] == 'intrusion_deep'

    def test_referential_preserves_viewpoint(self, context, inspect_action):
        """viewpoint 등 다른 theta key 보존."""
        rng = random.Random(0)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_RANDOM, context, rng,
        )
        assert out.theta['viewpoint'] == 'close'

    def test_referential_rejects_non_inspect(self, context, move_to_action):
        """referential variant + sigma!=inspect → ValueError."""
        with pytest.raises(ValueError, match='sigma=inspect'):
            apply_hallucination(
                move_to_action, FaultVariant.TARGET_SWAP_RANDOM,
                context, random.Random(0),
            )

    def test_referential_rejects_empty_candidates(self, inspect_action):
        """known_objects 가 orig 1개뿐이면 swap 후보 0 → ValueError."""
        ctx = FaultContext(
            known_objects={'book_on_coffee_table': (-1.8, 0.5, 0.45)},
            user_position=(0.0, 0.0, 0.0),
        )
        with pytest.raises(ValueError, match='swap 후보'):
            apply_hallucination(
                inspect_action, FaultVariant.TARGET_SWAP_RANDOM,
                ctx, random.Random(0),
            )

    def test_referential_nearest_rejects_missing_orig(self, context):
        """orig target 이 known_objects 측 없으면 nearest 불가."""
        action = TypedAction(
            sigma='inspect',
            theta={'target_id': 'unknown_object', 'viewpoint': 'close'},
        )
        with pytest.raises(ValueError, match='known_objects 측 위치'):
            apply_hallucination(
                action, FaultVariant.TARGET_SWAP_NEAREST,
                context, random.Random(0),
            )


# ------------------------------------------------- referential frequency (amend 16)


class TestFrequencyVariants:
    """amendment 16 D12c — target_swap_natural/amplified *빈도* variant.

    natural = referent_swap_rate 확률로 swap, amplified = 5× 확률. swap 시
    정책 = uniform random. 정책 variant (random/nearest/dangerous) 와 직교.
    """

    def _swap_count(self, variant, context, inspect_action, n=2000, seed=42):
        rng = random.Random(seed)
        orig = inspect_action.theta['target_id']
        swaps = 0
        for _ in range(n):
            out = apply_hallucination(inspect_action, variant, context, rng)
            assert out.sigma == 'inspect'
            assert out.theta['target_id'] in context.known_objects
            if out.theta['target_id'] != orig:
                swaps += 1
        return swaps

    def test_natural_frequency(self, context, inspect_action):
        """natural: swap 비율 ≈ referent_swap_rate (0.05). n=2000 이항 신뢰구간."""
        swaps = self._swap_count(
            FaultVariant.TARGET_SWAP_NATURAL, context, inspect_action,
        )
        rate = swaps / 2000.0
        # Binomial(2000, 0.05): mean 0.05, std ≈ 0.0049. ±4σ ≈ [0.030, 0.070].
        assert 0.030 < rate < 0.070, f'natural swap rate={rate:.4f}, expected ~0.05'

    def test_amplified_frequency(self, context, inspect_action):
        """amplified: swap 비율 ≈ 5 × referent_swap_rate (0.25). n=2000."""
        swaps = self._swap_count(
            FaultVariant.TARGET_SWAP_AMPLIFIED, context, inspect_action,
        )
        rate = swaps / 2000.0
        # Binomial(2000, 0.25): mean 0.25, std ≈ 0.0097. ±4σ ≈ [0.211, 0.289].
        assert 0.211 < rate < 0.289, f'amplified swap rate={rate:.4f}, expected ~0.25'

    def test_amplified_clamped_at_one(self, inspect_action):
        """referent_swap_rate=0.3 → amplified 5× = 1.5 → clamp 1.0 → 항상 swap."""
        ctx = FaultContext(
            known_objects={
                'book_on_coffee_table': (-1.8, 0.5, 0.45),  # orig
                'other_a': (1.0, 1.0, 0.5),
                'other_b': (2.0, 2.0, 0.5),
            },
            user_position=(0.0, -1.0, 1.1),
            referent_swap_rate=0.3,
        )
        rng = random.Random(0)
        orig = inspect_action.theta['target_id']
        for _ in range(50):
            out = apply_hallucination(
                inspect_action, FaultVariant.TARGET_SWAP_AMPLIFIED, ctx, rng,
            )
            assert out.theta['target_id'] != orig  # rate=1.0 → 항상 swap

    def test_natural_zero_rate_never_swaps(self, inspect_action):
        """referent_swap_rate=0 → natural 은 절대 swap 안 함 (정상 referent 유지)."""
        ctx = FaultContext(
            known_objects={
                'book_on_coffee_table': (-1.8, 0.5, 0.45),
                'other': (1.0, 1.0, 0.5),
            },
            user_position=(0.0, -1.0, 1.1),
            referent_swap_rate=0.0,
        )
        rng = random.Random(0)
        for _ in range(50):
            out = apply_hallucination(
                inspect_action, FaultVariant.TARGET_SWAP_NATURAL, ctx, rng,
            )
            assert out.theta['target_id'] == 'book_on_coffee_table'

    def test_frequency_swap_policy_is_random(self, context, inspect_action):
        """swap 발생 시 정책 = uniform random — 여러 후보가 등장."""
        rng = random.Random(7)
        seen = set()
        orig = inspect_action.theta['target_id']
        for _ in range(2000):
            out = apply_hallucination(
                inspect_action, FaultVariant.TARGET_SWAP_AMPLIFIED, context, rng,
            )
            tid = out.theta['target_id']
            if tid != orig:
                seen.add(tid)
        # 5 candidates — amplified(0.25)×2000 ≈ 500 swap 측 모든 후보 등장 가능.
        expected = set(context.known_objects.keys()) - {orig}
        assert seen == expected

    def test_frequency_preserves_viewpoint(self, context, inspect_action):
        """빈도 variant 도 다른 theta key 보존 (swap 발생 시)."""
        ctx = FaultContext(
            known_objects=dict(context.known_objects),
            user_position=context.user_position,
            referent_swap_rate=1.0,  # 항상 swap
        )
        rng = random.Random(0)
        out = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_NATURAL, ctx, rng,
        )
        assert out.theta['viewpoint'] == 'close'


# ----------------------------------------------------------- reproducibility


class TestReproducibility:
    """paper §C trial 측 seed 정합 재현성 보장."""

    def test_same_seed_same_output_gauss(self, context, move_to_action):
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        out_a = apply_hallucination(
            move_to_action, FaultVariant.POSITION_NOISE_GAUSS_LOW,
            context, rng_a,
        )
        out_b = apply_hallucination(
            move_to_action, FaultVariant.POSITION_NOISE_GAUSS_LOW,
            context, rng_b,
        )
        assert out_a.theta['position'] == out_b.theta['position']

    def test_same_seed_same_output_swap_random(self, context, inspect_action):
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        out_a = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_RANDOM,
            context, rng_a,
        )
        out_b = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_RANDOM,
            context, rng_b,
        )
        assert out_a.theta['target_id'] == out_b.theta['target_id']

    def test_different_seeds_different_outputs(self, context, inspect_action):
        """다른 seed → swap_random 측 다른 결과 가능 (확률적)."""
        out_a = apply_hallucination(
            inspect_action, FaultVariant.TARGET_SWAP_RANDOM,
            context, random.Random(1),
        )
        # 다른 seed 측 충돌 가능 — 여러 번 비교
        differ = False
        for s in range(2, 20):
            out_s = apply_hallucination(
                inspect_action, FaultVariant.TARGET_SWAP_RANDOM,
                context, random.Random(s),
            )
            if out_s.theta['target_id'] != out_a.theta['target_id']:
                differ = True
                break
        assert differ, '여러 seed 측 모두 동일 — random 측 분포 의문'


# ----------------------------------------------------------- unknown variant


class TestUnknownVariant:
    def test_unknown_variant_raises(self, context, move_to_action):
        """비-FaultVariant 입력 → ValueError."""
        with pytest.raises((ValueError, AttributeError)):
            apply_hallucination(
                move_to_action, 'not_a_variant',  # type: ignore
                context, random.Random(0),
            )
