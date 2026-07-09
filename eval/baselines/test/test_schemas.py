"""eval_baselines.schemas 단위 테스트."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.b3_context_aug import b3_config
from eval_baselines.b4_full_loop import b4_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestBaselineMode:
    def test_six_baselines_locked(self) -> None:
        """ADR-0025 D3 격자 정합 (amendment 19) — B0/B1a/B1b/B2/B3/B4 6 종."""
        values = {m.value for m in BaselineMode}
        assert values == {'b0', 'b1a', 'b1b', 'b2', 'b3', 'b4'}

    def test_str_inheritance(self) -> None:
        """str enum — YAML/JSON serialization 측 직접 비교 가능."""
        assert BaselineMode.B0 == 'b0'
        assert BaselineMode.B4.value == 'b4'


class TestBaselineConfig:
    def test_valid_b0(self) -> None:
        cfg = BaselineConfig(
            mode=BaselineMode.B0,
            tier1_mode='b0',
            context_aug=False,
            tier2_enabled=False,
        )
        assert cfg.mode == BaselineMode.B0
        assert cfg.tier1_mode == 'b0'
        assert cfg.context_aug is False
        assert cfg.tier2_enabled is False

    def test_valid_b1a(self) -> None:
        """B1a = 정적 r_min — tier1_mode='b1', intent layer / Tier 2 불활성."""
        cfg = BaselineConfig(
            mode=BaselineMode.B1A,
            tier1_mode='b1',
            context_aug=False,
            tier2_enabled=False,
        )
        assert cfg.mode == BaselineMode.B1A
        assert cfg.tier1_mode == 'b1'
        assert cfg.context_aug is False
        assert cfg.tier2_enabled is False

    def test_valid_b1b(self) -> None:
        """B1b = 정적 r_max — tier1_mode='b1_max', intent layer / Tier 2 불활성."""
        cfg = BaselineConfig(
            mode=BaselineMode.B1B,
            tier1_mode='b1_max',
            context_aug=False,
            tier2_enabled=False,
        )
        assert cfg.mode == BaselineMode.B1B
        assert cfg.tier1_mode == 'b1_max'
        assert cfg.context_aug is False
        assert cfg.tier2_enabled is False

    def test_valid_b2(self) -> None:
        """B2 = 신뢰도 변조 — tier1_mode='b2', intent layer / Tier 2 불활성."""
        cfg = BaselineConfig(
            mode=BaselineMode.B2,
            tier1_mode='b2',
            context_aug=False,
            tier2_enabled=False,
        )
        assert cfg.mode == BaselineMode.B2
        assert cfg.tier1_mode == 'b2'
        assert cfg.context_aug is False
        assert cfg.tier2_enabled is False

    def test_valid_b3(self) -> None:
        """B3 = 변조 + context — tier1_mode='b2', context_aug=True, Tier 2 불활성."""
        cfg = BaselineConfig(
            mode=BaselineMode.B3,
            tier1_mode='b2',
            context_aug=True,
            tier2_enabled=False,
        )
        assert cfg.mode == BaselineMode.B3
        assert cfg.tier1_mode == 'b2'
        assert cfg.context_aug is True
        assert cfg.tier2_enabled is False

    def test_valid_b4(self) -> None:
        cfg = BaselineConfig(
            mode=BaselineMode.B4,
            tier1_mode='b2',
            context_aug=True,
            tier2_enabled=True,
        )
        assert cfg.mode == BaselineMode.B4
        assert cfg.tier1_mode == 'b2'

    def test_frozen(self) -> None:
        cfg = BaselineConfig(
            mode=BaselineMode.B0,
            tier1_mode='b0',
            context_aug=False,
            tier2_enabled=False,
        )
        with pytest.raises(FrozenInstanceError):
            cfg.tier1_mode = 'b1'  # type: ignore[misc]

    def test_invalid_mode_type(self) -> None:
        with pytest.raises(TypeError, match='mode 는 BaselineMode'):
            BaselineConfig(
                mode='b0',  # type: ignore[arg-type]
                tier1_mode='b0',
                context_aug=False,
                tier2_enabled=False,
            )

    def test_invalid_tier1_mode(self) -> None:
        with pytest.raises(ValueError, match='tier1_mode'):
            BaselineConfig(
                mode=BaselineMode.B0,
                tier1_mode='b9',
                context_aug=False,
                tier2_enabled=False,
            )

    def test_tier1_mode_must_be_lowercase(self) -> None:
        """tier1_filter.FilterMode 와 정합 — 소문자만 허용."""
        with pytest.raises(ValueError, match='tier1_mode'):
            BaselineConfig(
                mode=BaselineMode.B0,
                tier1_mode='B0',
                context_aug=False,
                tier2_enabled=False,
            )

    def test_invalid_context_aug_type(self) -> None:
        with pytest.raises(TypeError, match='context_aug 는 bool'):
            BaselineConfig(
                mode=BaselineMode.B0,
                tier1_mode='b0',
                context_aug=1,  # type: ignore[arg-type]
                tier2_enabled=False,
            )

    def test_invalid_tier2_enabled_type(self) -> None:
        with pytest.raises(TypeError, match='tier2_enabled 는 bool'):
            BaselineConfig(
                mode=BaselineMode.B0,
                tier1_mode='b0',
                context_aug=False,
                tier2_enabled=None,  # type: ignore[arg-type]
            )

    def test_tier2_without_context_aug_rejected(self) -> None:
        """paper §C 격자 정의 — B4 만 tier2_enabled=True, B4 는 context_aug=True.

        context_aug=False + tier2_enabled=True 조합은 격자에 없음 → 거부.
        """
        with pytest.raises(ValueError, match='context_aug=True 필요'):
            BaselineConfig(
                mode=BaselineMode.B4,
                tier1_mode='b2',
                context_aug=False,
                tier2_enabled=True,
            )

    def test_context_aug_without_tier2_allowed(self) -> None:
        """B3 = context_aug=True + tier2_enabled=False — 격자에 정의됨."""
        cfg = BaselineConfig(
            mode=BaselineMode.B3,
            tier1_mode='b2',
            context_aug=True,
            tier2_enabled=False,
        )
        assert cfg.context_aug is True
        assert cfg.tier2_enabled is False


class TestAblationChainInvariant:
    """6 baseline 누적 ablation chain 측 *단축 단일 축 차이* invariant.

    PR #118 review T-3 lesson — paper §C 표 1 의 ablation 의 의미 = 각 인접
    chain step 측 *정확히 1 축* 차이. 6 baseline 전체 chain 잠금 (B0 → B1a →
    B1b → B2 → B3 → B4) — 각 step 측 차이 축 명시 (amendment 19).

    이 invariant 가 깨지면 paper §C 표 1 의 ablation 해석 무의미 (다축 변경 시
    효과 *분리 측정* 불가능).
    """

    def _diff_axes(self, a: BaselineConfig, b: BaselineConfig) -> tuple[str, ...]:
        diffs = []
        if a.tier1_mode != b.tier1_mode:
            diffs.append('tier1_mode')
        if a.context_aug != b.context_aug:
            diffs.append('context_aug')
        if a.tier2_enabled != b.tier2_enabled:
            diffs.append('tier2_enabled')
        return tuple(diffs)

    def test_b0_to_b1a_differs_in_tier1_mode_only(self) -> None:
        assert self._diff_axes(b0_config(), b1a_config()) == ('tier1_mode',)

    def test_b1a_to_b1b_differs_in_tier1_mode_only(self) -> None:
        assert self._diff_axes(b1a_config(), b1b_config()) == ('tier1_mode',)

    def test_b1b_to_b2_differs_in_tier1_mode_only(self) -> None:
        assert self._diff_axes(b1b_config(), b2_config()) == ('tier1_mode',)

    def test_b2_to_b3_differs_in_context_aug_only(self) -> None:
        assert self._diff_axes(b2_config(), b3_config()) == ('context_aug',)

    def test_b3_to_b4_differs_in_tier2_enabled_only(self) -> None:
        assert self._diff_axes(b3_config(), b4_config()) == ('tier2_enabled',)

    def test_all_six_baselines_distinct(self) -> None:
        """B0/B1a/B1b/B2/B3/B4 6 종 모두 *서로 다른* config (mode 식별자 포함)."""
        configs = [b0_config(), b1a_config(), b1b_config(), b2_config(), b3_config(), b4_config()]
        assert len(set(configs)) == 6, "6 baseline 모두 distinct 해야 함"

    def test_b0_to_b4_full_chain_cumulative_diff(self) -> None:
        """B0 → B4 측 누적 차이 = 3 축 (tier1_mode + context_aug + tier2_enabled).

        chain step 별 단축 차이 합 (1+1+1+1+1=5) 과 *총 차이 축 수* (3) 의 분리 —
        B0 → B1a → B1b → B2 측 tier1_mode 가 *반복 변경* 됨 (b0→b1→b1_max→b2).
        즉 chain step cumsum 과 endpoint diff 의 *불일치* 측면 의도 정합 검증.
        """
        b0 = b0_config()
        b4 = b4_config()
        diff = self._diff_axes(b0, b4)
        assert set(diff) == {'tier1_mode', 'context_aug', 'tier2_enabled'}
