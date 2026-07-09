# intent/

LLM 의미·의도 계층. **결정론 안전 계층(/safety)과 분리** — 여기서 무슨 짓을 하든 /safety의 안전 보증은 무너지지 않아야 함.

## Layout

- `llm/` — LLM 백본 인터페이스. 추상화는 백본 교체에 무관하게(C4 ablation 분석 = LLM불가지성 입증). 입력 = 저대역폭 사용자 입력(음성/시선/EMG 추상화) + context. 출력 = **타입드 목표 + 신뢰도** (제한 스킬 API 형식).
- `confidence/` — 신뢰도 추정. 접지 엔트로피, 자기일관성, calibration. 출력은 $[0, 1]$ 스칼라 (또는 분포). /safety/tier1이 이 값을 받아 $r(c)$·CBF 보수성을 변조.

## 주의

- 1차 타깃 사용자 = **사지마비(C4–C6) 페르소나** ([CLAUDE.md §A4](../CLAUDE.md)). 입력 modality 설계는 저대역폭 가정.
- 신뢰도 추정 방법은 [RESEARCH_CONTEXT §B6](../docs/RESEARCH_CONTEXT.md#b6-1차-논문-계획) §6 ablation 분석에서 평가.
