"""eval_baselines.b2_modulated 단위 테스트.

ADR-0025 D3 격자 정의 — B2 = (b2, False, False) 잠금 검증.
"""

from __future__ import annotations

from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.schemas import BaselineConfig, BaselineMode


class TestB2Config:
    def test_returns_baseline_config(self) -> None:
        cfg = b2_config()
        assert isinstance(cfg, BaselineConfig)

    def test_mode_is_b2(self) -> None:
        assert b2_config().mode == BaselineMode.B2

    def test_tier1_mode_is_b2(self) -> None:
        """B2 trial = tier1_filter mode='b2' (신뢰도 변조 r(c̃) CBF-QP).

        cmsm-proof §6 정리 2 정합 — 시변 c̃(t) 안전집합 전방불변성 보장.
        """
        assert b2_config().tier1_mode == 'b2'

    def test_context_aug_disabled(self) -> None:
        """B2 = intent layer (context augmentation) 불활성.

        주의: 신뢰도 c 자체는 외부 source (fault injector 또는 estimator_node)
        가 publish — context_aug 는 LLM 측 *context graph + ego-stream 융합*
        활성화 여부 (paper §6 fusion) 이며 c publish 여부와 별개.
        """
        assert b2_config().context_aug is False

    def test_tier2_disabled(self) -> None:
        """B2 = Tier 2 게이트 불활성."""
        assert b2_config().tier2_enabled is False

    def test_deterministic(self) -> None:
        """b2_config() 호출 측 idempotent (BaselineConfig frozen)."""
        assert b2_config() == b2_config()

    def test_full_contract(self) -> None:
        """ADR-0025 D3 B2 정의 전체 잠금 — 신뢰도 변조 baseline."""
        cfg = b2_config()
        assert cfg == BaselineConfig(
            mode=BaselineMode.B2,
            tier1_mode='b2',
            context_aug=False,
            tier2_enabled=False,
        )

    def test_differs_from_b1b_only_in_tier1_mode(self) -> None:
        """B1b vs B2 = tier1_mode 단독 차이 (paper-1 C2 핵심 ablation).

        paper §C 표 1 의 B1b → B2 행 비교 = *신뢰도 변조의 안전-과보수성 trade-off*
        효과 측정. paper-1 의 핵심 기여 (C2 신뢰도-변조 CBF + 단조성-하한 불변식)
        입증의 직접 자리 (amendment 19: B1→B1b).
        """
        b1b = b1b_config()
        b2 = b2_config()
        assert b1b.tier1_mode != b2.tier1_mode
        assert b1b.context_aug == b2.context_aug
        assert b1b.tier2_enabled == b2.tier2_enabled
        assert b1b.mode != b2.mode
