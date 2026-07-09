"""eval_faults — paper §C fault injection 모듈.

ADR-0025 D1 의 4 fault_class (hallucination · adversarial · cognitive_lapse ·
attribute_mismatch) 별 hook 함수 + scenario YAML 측 fault_variant 매핑.

본 패키지의 PR 단위 (D5 12 PR 시안):
  B5 #1: hallucination.py — post-LLM σ hook (positional 3 + referential 3)
  B5 #2: adversarial.py — prompt 측 OWASP LLM01 injection
  B5 #3: cognitive_lapse.py — 시간축 측 E1-E4
  B5 #4: attribute_mismatch.py — OVD detection 측 오탐
  B5 #5: injector_node.py — ROS 2 wiring + smoke

본 PR (B5 #1) scope = hallucination.py + schemas + 단위 테스트만.
"""
