"""intent_llm — paper §C 5-way 의도해석기 wrapper 패키지.

[ADR-0018 D3](../../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d3)
+ [ADR-0005 D6](../../../docs/handover/decisions/0005-paper1-framing.md#d6)
+ [ADR-0014](../../../docs/handover/decisions/0014-llm-backbone-six-lock.md) 정합.

5 카테고리 × 9 식별자 ablation:
  - Cloud LLM (3): GPT-5.5 · GPT-5 · GPT-4o
  - Edge LLM (3): Gemma 4 E4B · Qwen2.5-VL 7B · Llama 3.2 11B-Vision
  - 7B VLA (1): OpenVLA-7B
  - Domain classifier (1): closed-vocabulary
  - Adversarial (1): ADR-0014 6종 중 wrapping

## PR 분할 (B7 #12 분할 2b)

  - 2b-1 (✅ PR #124 / #125): interface · skill_catalog · classifier · registry
  - 2b-2 (✅ PR #126 / #127): cloud_llm · edge_llm · backbones (Cloud 3 + Edge 3)
  - 2b-3 (✅ PR #129 / #132): vla — OpenVLA-7B mock (ADR-0018 D3 row 3 + §A3)
  - 2b-4 (본 PR): adversarial — AdversarialWrapper (ADR-0018 D3 row 5 + D5
    OWASP LLM01 prompt injection + GPT-4o wrap + skill swap + confidence
    inflation + signals 왜곡)

→ 5-way ablation **완전 cover** (9 식별자 등록).
"""
