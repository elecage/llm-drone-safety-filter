# safety/

3-티어 안전 스택. **의도 계층(/intent)과 독립** — LLM을 교체해도 무영향이어야 함 ([CLAUDE.md §A4](../CLAUDE.md) C1·LLM불가지성).

## 티어 (자세히: [RESEARCH_CONTEXT §B5](../docs/RESEARCH_CONTEXT.md#b5))

- `tier0/` — **PX4 펌웨어 failsafe.** 지오펜스·속도/틸트 클램프·RTL·비상정지. PX4 파라미터 YAML 형태로 관리(`px4_params.yaml`). LLM·ROS에서 우회 불가.
- `tier1/` — **실시간 반응형 안전 필터** (≥20Hz, 결정론). 사용자 동적 회피 영역, CBF-QP. 입력 setpoint를 클램프/거부하는 ROS 2 노드. 신뢰도-변조 CBF의 핵심 구현부.
- `tier2/` — **계획 수준 런타임 검증 게이트.** 제한 스킬 API + 시간논리 안전 사양. 신뢰도 임계 미만이면 후퇴/확인. LLM 인지(이벤트 기반).
- `common/` — 세 티어가 공유하는 타입·메시지·유틸 (e.g., `SafetyState`, 사용자 위치 추정 인터페이스).

## 연구 정합성

- CBF-QP·전방불변성 수식은 Ames 2017 차용 — 코드 주석/논문 모두 인용 필수.
- $r(c)$·단조성-하한 불변식은 **제안(미증명)** — 코드도 "proposed" 명명, 증명 노트는 [docs/RESEARCH_CONTEXT §B7](../docs/RESEARCH_CONTEXT.md#b7-수식-출처--연구-정합성-상세--절대-삭제-금지) 참조.
