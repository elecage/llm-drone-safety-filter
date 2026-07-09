"""eval_baselines.b1b_static_rmax 단위 테스트.

ADR-0025 D3 격자 정의 (amendment 19 — B1→B1a/B1b 분리):
B1b = (b1_max, False, False) 잠금 검증. 정적 $r_\\text{max}$ baseline.
"""

from __future__ import annotations

from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestB1bConfig:
    def test_returns_baseline_config(self) -> None:
        cfg = b1b_config()
        assert isinstance(cfg, BaselineConfig)

    def test_mode_is_b1b(self) -> None:
        assert b1b_config().mode == BaselineMode.B1B

    def test_tier1_mode_is_b1_max(self) -> None:
        """B1b trial = tier1_filter mode='b1_max' (정적 r_max CBF-QP).

        cmsm-proof §5 명제 1 정합 — 신뢰도 입력 없이 r=r_max 고정.
        B1a 대비 더 보수적 (큰 회피 영역) → 안전 위반 없으나 과보수성 상승.
        """
        assert b1b_config().tier1_mode == 'b1_max'

    def test_context_aug_disabled(self) -> None:
        """B1b = intent layer 불활성 (B3 와 비교 시 context_aug 단독 차이)."""
        assert b1b_config().context_aug is False

    def test_tier2_disabled(self) -> None:
        """B1b = Tier 2 게이트 불활성 (B4 와 비교 시 다축 차이)."""
        assert b1b_config().tier2_enabled is False

    def test_deterministic(self) -> None:
        """b1b_config() 호출 측 idempotent (BaselineConfig frozen)."""
        assert b1b_config() == b1b_config()

    def test_full_contract(self) -> None:
        """ADR-0025 D3 B1b 정의 전체 잠금 — 정적 r_max baseline."""
        cfg = b1b_config()
        assert cfg == BaselineConfig(
            mode=BaselineMode.B1B,
            tier1_mode='b1_max',
            context_aug=False,
            tier2_enabled=False,
        )

    def test_differs_from_b1a_only_in_tier1_mode(self) -> None:
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

    def test_differs_from_b2_only_in_tier1_mode(self) -> None:
        """B1b vs B2 = tier1_mode 단독 차이 ('b1_max' vs 'b2').

        paper §C 표 1 에서 B1b → B2 행 비교 = *신뢰도 변조 효과* 측정.
        B1b(정적 r_max) vs B2(동적 r(c)) — context_aug·tier2_enabled 동일.
        """
        b1b = b1b_config()
        b2 = b2_config()
        assert b1b.tier1_mode == 'b1_max'
        assert b2.tier1_mode == 'b2'
        assert b1b.context_aug == b2.context_aug
        assert b1b.tier2_enabled == b2.tier2_enabled
        assert b1b.mode != b2.mode
