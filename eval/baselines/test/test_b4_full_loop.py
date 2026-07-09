"""eval_baselines.b4_full_loop 단위 테스트.

ADR-0025 D3 격자 정의 — B4 = (b2, True, True) 잠금 검증.
"""

from __future__ import annotations

from eval_baselines.b3_context_aug import b3_config
from eval_baselines.b4_full_loop import b4_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestB4Config:
    def test_returns_baseline_config(self) -> None:
        cfg = b4_config()
        assert isinstance(cfg, BaselineConfig)

    def test_mode_is_b4(self) -> None:
        assert b4_config().mode == BaselineMode.B4

    def test_tier1_mode_is_b2(self) -> None:
        """B4 trial = tier1_filter mode='b2' (B2/B3 와 동일 — 차이는 intent layer 측)."""
        assert b4_config().tier1_mode == 'b2'

    def test_context_aug_enabled(self) -> None:
        """B4 = context augmentation 활성화 (B3 와 공통)."""
        assert b4_config().context_aug is True

    def test_tier2_enabled(self) -> None:
        """B4 의 *고유 특성* — Tier 2 시간논리 사양 게이트 활성화.

        ADR-0017 + ADR-0025 D1 정합 — Φ_1/Φ_2/Φ_3/Φ_8/Φ_9/Φ_10 등 *계획 수준*
        안전 사양 강제. paper-1 C3 기여 (계획 수준 런타임 검증 게이트) 입증 자리.
        """
        assert b4_config().tier2_enabled is True

    def test_deterministic(self) -> None:
        """b4_config() 호출 측 idempotent (BaselineConfig frozen)."""
        assert b4_config() == b4_config()

    def test_full_contract(self) -> None:
        """ADR-0025 D3 B4 정의 전체 잠금 — 전체 아키텍처 baseline."""
        cfg = b4_config()
        assert cfg == BaselineConfig(
            mode=BaselineMode.B4,
            tier1_mode='b2',
            context_aug=True,
            tier2_enabled=True,
        )

    def test_differs_from_b3_only_in_tier2(self) -> None:
        """B3 vs B4 = tier2_enabled 단독 차이 (ablation 의 핵심 의미).

        paper §C 표 1 의 B3 → B4 행 비교 = *Tier 2 게이트의 계획 수준 결함
        조기 차단 효과* 측정. 가설: V 추가 감소 + ARS 유지 + QR 측 정량 변화
        (Tier 2 가 ask_user 후퇴 trigger).
        """
        b3 = b3_config()
        b4 = b4_config()
        assert b3.tier1_mode == b4.tier1_mode  # 둘 다 'b2'
        assert b3.context_aug == b4.context_aug  # 둘 다 True
        assert b3.tier2_enabled != b4.tier2_enabled
        assert b3.mode != b4.mode

        # PR #118 review C-1 lesson — *정확히 1 축 차이* invariant 강화.
        diff_count = sum([
            b3.tier1_mode != b4.tier1_mode,
            b3.context_aug != b4.context_aug,
            b3.tier2_enabled != b4.tier2_enabled,
        ])
        assert diff_count == 1, "B3 vs B4 ablation must differ in exactly one axis"
