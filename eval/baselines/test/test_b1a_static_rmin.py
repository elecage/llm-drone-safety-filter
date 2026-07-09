"""eval_baselines.b1a_static_rmin 단위 테스트.

ADR-0025 D3 격자 정의 (amendment 19 — B1→B1a/B1b 분리):
B1a = (b1, False, False) 잠금 검증. 정적 $r_\\text{min}$ baseline.
"""

from __future__ import annotations

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestB1aConfig:
    def test_returns_baseline_config(self) -> None:
        cfg = b1a_config()
        assert isinstance(cfg, BaselineConfig)

    def test_mode_is_b1a(self) -> None:
        assert b1a_config().mode == BaselineMode.B1A

    def test_tier1_mode_is_b1(self) -> None:
        """B1a trial = tier1_filter mode='b1' (정적 r_min CBF-QP).

        cmsm-proof §5 명제 1 정합 — 신뢰도 입력 없이 r=r_min 고정.
        """
        assert b1a_config().tier1_mode == 'b1'

    def test_context_aug_disabled(self) -> None:
        """B1a = intent layer 불활성 (B3 와 비교 시 context_aug 단독 차이)."""
        assert b1a_config().context_aug is False

    def test_tier2_disabled(self) -> None:
        """B1a = Tier 2 게이트 불활성 (B4 와 비교 시 다축 차이)."""
        assert b1a_config().tier2_enabled is False

    def test_deterministic(self) -> None:
        """b1a_config() 호출 측 idempotent (BaselineConfig frozen)."""
        assert b1a_config() == b1a_config()

    def test_full_contract(self) -> None:
        """ADR-0025 D3 B1a 정의 전체 잠금 — 정적 r_min baseline."""
        cfg = b1a_config()
        assert cfg == BaselineConfig(
            mode=BaselineMode.B1A,
            tier1_mode='b1',
            context_aug=False,
            tier2_enabled=False,
        )

    def test_differs_from_b0_only_in_tier1_mode(self) -> None:
        """B0 vs B1a = tier1_mode 단독 차이 (ablation 의 핵심 의미).

        paper §C 표 1 에서 B0 → B1a 행 비교 = *정적 r_min 마진 안전 필터의 효과* 측정.
        """
        b0 = b0_config()
        b1a = b1a_config()
        assert b0.tier1_mode != b1a.tier1_mode
        assert b0.context_aug == b1a.context_aug
        assert b0.tier2_enabled == b1a.tier2_enabled
        assert b0.mode != b1a.mode

    def test_differs_from_b1b_only_in_tier1_mode(self) -> None:
        """B1a vs B1b = tier1_mode 단독 차이 ('b1' vs 'b1_max').

        두 baseline 모두 정적 마진이되, B1a(효율 끝점)는 r_min,
        B1b(안전 끝점)는 r_max 사용 — paper §C 표 1 트레이드오프 곡선의 끝점.
        """
        b1a = b1a_config()
        b1b = b1b_config()
        assert b1a.tier1_mode == 'b1'
        assert b1b.tier1_mode == 'b1_max'
        assert b1a.context_aug == b1b.context_aug
        assert b1a.tier2_enabled == b1b.tier2_enabled
        assert b1a.mode != b1b.mode
