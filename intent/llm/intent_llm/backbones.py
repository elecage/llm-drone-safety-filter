"""ADR-0014 6 백본 식별자 + metadata 잠금.

[ADR-0014 D1](../../../docs/handover/decisions/0014-llm-backbone-six-lock.md#d1)
6 백본 표 source-of-truth — Cloud 3 (GPT-4o · GPT-5 · GPT-5.5) + Edge 3
(Gemma 4 E4B · Qwen2.5-VL 7B · Llama 3.2 11B-Vision).

각 백본 측 (generation · variant · lab · model_size) 측 식별자 metadata —
paper §C 측 ablation 측 *세대 별 비교* (축 A) + *Local vs Cloud variant* 비교
(축 B) 측 직접 인용.

ADR-0014 D5 ablation 축:
  - 축 A — Cloud 세대 비교: GPT-4o (2024) vs GPT-5 (2025) vs GPT-5.5 (2026)
  - 축 B — Local vs Cloud variant (동세대):
    - 2024: Llama 3.2 vs GPT-4o
    - 2025: Qwen2.5-VL vs GPT-5
    - 2026: Gemma 4 E4B vs GPT-5.5

## 미구현 노드 표시

본 PR (B7 #12 분할 2b-2) scope = *mock contract* 만. 실 API call (Cloud) / 실
local inference (Edge, Ollama 측 llama.cpp Metal wrapper) 측 후속 PR 측.
ADR-0014 D2 측 backend 측 *llama.cpp Metal 또는 Apple MLX* 잠금 — Ollama 측
호환 (paper §C edge LLM 추론 백엔드 검토 결과 정합, 2026-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class BackboneVariant(str, Enum):
    """ADR-0014 D1 — Local (edge) vs Cloud."""

    LOCAL = 'local'
    CLOUD = 'cloud'


class BackboneGeneration(str, Enum):
    """ADR-0014 D4 — release year 기준 세대 명명."""

    GEN_2024 = '2024'  # 두 세대 전
    GEN_2025 = '2025'  # 전세대
    GEN_2026 = '2026'  # 현세대


@dataclass(frozen=True)
class BackboneSpec:
    """ADR-0014 D1 표 row 측 단일 백본 사양.

    Attributes
    ----------
    identifier : str
        wrapper 측 registry key — 'gpt-4o' · 'gemma-4-e4b' 등.
    variant : BackboneVariant
        LOCAL (edge) | CLOUD.
    generation : BackboneGeneration
        2024 | 2025 | 2026.
    lab : str
        모델 발표 lab — 'OpenAI' · 'Google DeepMind' · 'Alibaba' · 'Meta'.
    """

    identifier: str
    variant: BackboneVariant
    generation: BackboneGeneration
    lab: str


# ADR-0014 D1 표 source-of-truth — 6 백본 잠금.
GPT_4O = BackboneSpec(
    identifier='gpt-4o',
    variant=BackboneVariant.CLOUD,
    generation=BackboneGeneration.GEN_2024,
    lab='OpenAI',
)
GPT_5 = BackboneSpec(
    identifier='gpt-5',
    variant=BackboneVariant.CLOUD,
    generation=BackboneGeneration.GEN_2025,
    lab='OpenAI',
)
GPT_55 = BackboneSpec(
    identifier='gpt-5.5',
    variant=BackboneVariant.CLOUD,
    generation=BackboneGeneration.GEN_2026,
    lab='OpenAI',
)
LLAMA_32_11B_VISION = BackboneSpec(
    identifier='llama-3.2-11b-vision',
    variant=BackboneVariant.LOCAL,
    generation=BackboneGeneration.GEN_2024,
    lab='Meta',
)
QWEN_25_VL_7B = BackboneSpec(
    identifier='qwen2.5-vl-7b',
    variant=BackboneVariant.LOCAL,
    generation=BackboneGeneration.GEN_2025,
    lab='Alibaba',
)
GEMMA_4_E4B = BackboneSpec(
    identifier='gemma-4-e4b',
    variant=BackboneVariant.LOCAL,
    generation=BackboneGeneration.GEN_2026,
    lab='Google DeepMind',
)


# 6 백본 잠금 — variant 별 grouping (Cloud 3 → Edge 3) + 세대 오름차순 (2024 →
# 2026) 내부 정렬. ADR-0014 D1 표 row 순서 측 *2026 Local 첫* (Gemma 4) 측
# *variant 교차* — paper §C 측 ablation 읽기 측 *grouped readout* (Cloud
# 세대 비교 → Edge 세대 비교) 측 정합 위해 본 구조 채택. PR #126 review C-1
# 정정 — 이전 docstring "ADR-0014 D1 표 row 순서" 측 실 order 측 불일치 (honesty
# critical) → 본 grouping 측 정확한 명시.
#
# 본 order 측 *registry insertion* 측 영향: list_registered() 측 sorted 측면
# 측 alphabetical (영향 없음). 단, ALL_BACKBONES 측 iteration 측 *Cloud 3 먼저
# → Edge 3 다음* — test 측 enumeration 측 본 가정 의존 (예:
# test_six_backbones_six_distinct_signatures 측 6 distinct sig 측 order-independent
# OK).
ALL_BACKBONES: Tuple[BackboneSpec, ...] = (
    GPT_4O, GPT_5, GPT_55,
    LLAMA_32_11B_VISION, QWEN_25_VL_7B, GEMMA_4_E4B,
)


# variant 별 grouping — registry.py 측 cloud 3 + edge 3 등록 측 사용.
CLOUD_BACKBONES: Tuple[BackboneSpec, ...] = tuple(
    b for b in ALL_BACKBONES if b.variant == BackboneVariant.CLOUD
)
EDGE_BACKBONES: Tuple[BackboneSpec, ...] = tuple(
    b for b in ALL_BACKBONES if b.variant == BackboneVariant.LOCAL
)
