"""eval_baselines.b3_context_aug 단위 테스트.

ADR-0025 D3 격자 정의 — B3 = (b2, True, False) 잠금 검증.
"""

from __future__ import annotations

from eval_baselines.b2_modulated import b2_config
from eval_baselines.b3_context_aug import b3_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestB3Config:
    def test_returns_baseline_config(self) -> None:
        cfg = b3_config()
        assert isinstance(cfg, BaselineConfig)

    def test_mode_is_b3(self) -> None:
        assert b3_config().mode == BaselineMode.B3

    def test_tier1_mode_is_b2(self) -> None:
        """B3 trial = tier1_filter mode='b2' (B2 와 동일 — 차이는 intent layer 측).

        B3 의 *변조* 측면 = B2 와 동일 (둘 다 신뢰도 변조 CBF-QP). 차이는 LLM
        입력 측 context augmentation 여부 — tier1 측은 동일 동작.
        """
        assert b3_config().tier1_mode == 'b2'

    def test_context_aug_enabled(self) -> None:
        """B3 = context augmentation 활성화 (paper §6 fusion).

        B3 의 고유 특성. LLM 입력에 context graph + ego-stream 융합 → grounding
        accuracy ↑ → c̃ 체계적으로 더 높게 → r(c̃) → r_min → 과보수성 개선.
        """
        assert b3_config().context_aug is True

    def test_tier2_disabled(self) -> None:
        """B3 = Tier 2 게이트 불활성 (B4 와 비교 시 tier2_enabled 단독 차이)."""
        assert b3_config().tier2_enabled is False

    def test_deterministic(self) -> None:
        """b3_config() 호출 측 idempotent (BaselineConfig frozen)."""
        assert b3_config() == b3_config()

    def test_full_contract(self) -> None:
        """ADR-0025 D3 B3 정의 전체 잠금 — context-augmented baseline."""
        cfg = b3_config()
        assert cfg == BaselineConfig(
            mode=BaselineMode.B3,
            tier1_mode='b2',
            context_aug=True,
            tier2_enabled=False,
        )

    def test_differs_from_b2_only_in_context_aug(self) -> None:
        """B2 vs B3 = context_aug 단독 차이 (ablation 의 핵심 의미).

        paper §C 표 1 에서 B2 → B3 행 비교 = *context augmentation 의 grounding
        error 감소 효과* 측정. 가설: B3 가 B2 대비 *과보수성* $\\bar r$ 감소
        + 안전 유지 (안전 위반율 V 동일 또는 ↓).
        """
        b2 = b2_config()
        b3 = b3_config()
        assert b2.tier1_mode == b3.tier1_mode  # 둘 다 'b2'
        assert b2.context_aug != b3.context_aug
        assert b2.tier2_enabled == b3.tier2_enabled  # 둘 다 False
        assert b2.mode != b3.mode
