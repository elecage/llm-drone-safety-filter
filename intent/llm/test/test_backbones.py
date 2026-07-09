"""intent_llm.backbones 단위 테스트.

ADR-0014 D1 6 백본 표 source-of-truth 잠금 — identifier · variant · generation
· lab.
"""

from __future__ import annotations

from intent_llm.backbones import (
    ALL_BACKBONES,
    CLOUD_BACKBONES,
    EDGE_BACKBONES,
    GEMMA_4_E4B,
    GPT_4O,
    GPT_5,
    GPT_55,
    LLAMA_32_11B_VISION,
    QWEN_25_VL_7B,
    BackboneGeneration,
    BackboneSpec,
    BackboneVariant,
)


class TestEnums:
    def test_variant_values(self) -> None:
        assert BackboneVariant.LOCAL.value == 'local'
        assert BackboneVariant.CLOUD.value == 'cloud'

    def test_generation_values(self) -> None:
        assert BackboneGeneration.GEN_2024.value == '2024'
        assert BackboneGeneration.GEN_2025.value == '2025'
        assert BackboneGeneration.GEN_2026.value == '2026'


class TestBackboneCount:
    def test_six_backbones(self) -> None:
        """ADR-0014 D1 = 6 백본 잠금."""
        assert len(ALL_BACKBONES) == 6

    def test_cloud_three(self) -> None:
        assert len(CLOUD_BACKBONES) == 3

    def test_edge_three(self) -> None:
        assert len(EDGE_BACKBONES) == 3

    def test_cloud_plus_edge_equals_all(self) -> None:
        assert len(CLOUD_BACKBONES) + len(EDGE_BACKBONES) == len(ALL_BACKBONES)


class TestBackboneIdentifiers:
    """ADR-0014 D1 표 row identifier — *문자열 잠금* (paper §C 인용)."""

    def test_gpt_4o_identifier(self) -> None:
        assert GPT_4O.identifier == 'gpt-4o'
        assert GPT_4O.variant == BackboneVariant.CLOUD
        assert GPT_4O.generation == BackboneGeneration.GEN_2024
        assert GPT_4O.lab == 'OpenAI'

    def test_gpt_5_identifier(self) -> None:
        assert GPT_5.identifier == 'gpt-5'
        assert GPT_5.generation == BackboneGeneration.GEN_2025

    def test_gpt_55_identifier(self) -> None:
        assert GPT_55.identifier == 'gpt-5.5'
        assert GPT_55.generation == BackboneGeneration.GEN_2026

    def test_llama_identifier(self) -> None:
        assert LLAMA_32_11B_VISION.identifier == 'llama-3.2-11b-vision'
        assert LLAMA_32_11B_VISION.variant == BackboneVariant.LOCAL
        assert LLAMA_32_11B_VISION.lab == 'Meta'

    def test_qwen_identifier(self) -> None:
        assert QWEN_25_VL_7B.identifier == 'qwen2.5-vl-7b'
        assert QWEN_25_VL_7B.lab == 'Alibaba'

    def test_gemma_identifier(self) -> None:
        assert GEMMA_4_E4B.identifier == 'gemma-4-e4b'
        assert GEMMA_4_E4B.lab == 'Google DeepMind'


class TestUniqueness:
    def test_identifiers_distinct(self) -> None:
        ids = [b.identifier for b in ALL_BACKBONES]
        assert len(set(ids)) == len(ids)


class TestADR0014D5AblationAxes:
    """ADR-0014 D5 ablation 축 정합 — 세대 별 + variant 별 grouping."""

    def test_axis_a_cloud_generation_coverage(self) -> None:
        """축 A — Cloud 측 3 세대 모두 cover."""
        gens = {b.generation for b in CLOUD_BACKBONES}
        assert gens == set(BackboneGeneration)

    def test_axis_b_local_generation_coverage(self) -> None:
        """축 B — Local 측 3 세대 모두 cover (동세대 local-cloud 쌍 비교)."""
        gens = {b.generation for b in EDGE_BACKBONES}
        assert gens == set(BackboneGeneration)

    def test_local_cloud_pair_per_generation(self) -> None:
        """각 세대 측 정확히 1 local + 1 cloud 백본 — ADR-0014 D5 축 B 정합."""
        for gen in BackboneGeneration:
            same_gen = [b for b in ALL_BACKBONES if b.generation == gen]
            assert len(same_gen) == 2
            variants = {b.variant for b in same_gen}
            assert variants == {BackboneVariant.LOCAL, BackboneVariant.CLOUD}


class TestFrozen:
    def test_backbone_spec_frozen(self) -> None:
        import pytest
        with pytest.raises((AttributeError, Exception)):
            GPT_4O.identifier = 'changed'  # type: ignore[misc]
