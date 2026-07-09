"""eval_baselines.b0_passthrough 단위 테스트.

ADR-0025 D3 격자 정의 — B0 = (b0, False, False) 잠금 검증.
"""

from __future__ import annotations

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestB0Config:
    def test_returns_baseline_config(self) -> None:
        cfg = b0_config()
        assert isinstance(cfg, BaselineConfig)

    def test_mode_is_b0(self) -> None:
        assert b0_config().mode == BaselineMode.B0

    def test_tier1_mode_is_b0(self) -> None:
        """B0 trial = tier1_filter mode='b0' (pass-through, 필터 없음)."""
        assert b0_config().tier1_mode == 'b0'

    def test_context_aug_disabled(self) -> None:
        """B0 = intent layer 불활성."""
        assert b0_config().context_aug is False

    def test_tier2_disabled(self) -> None:
        """B0 = Tier 2 게이트 불활성."""
        assert b0_config().tier2_enabled is False

    def test_deterministic(self) -> None:
        """b0_config() 호출 측 idempotent (BaselineConfig frozen)."""
        assert b0_config() == b0_config()

    def test_full_contract(self) -> None:
        """ADR-0025 D3 B0 정의 전체 잠금 — 3 축 모두 비활성 baseline."""
        cfg = b0_config()
        assert cfg == BaselineConfig(
            mode=BaselineMode.B0,
            tier1_mode='b0',
            context_aug=False,
            tier2_enabled=False,
        )
