# sim/scenarios/

시나리오별 명세·구현 자산. **시나리오 축 정본 = [ADR-0039 D2](../../docs/handover/decisions/0039-full-experiment-matrix.md)** (ADR-0006 초기 셋을 supersede — 결함·게이트·RQ1을 직교 축으로 분리하면서 시나리오 축을 *C2 신뢰도 스펙트럼 전용*으로 재정의). paper 라벨 매핑 = [ADR-0044](../../docs/handover/decisions/0044-scenario-assumption-label-renumber.md) (코드 ID S5·S6 ↔ paper S1·S2).

## 본실험 시나리오 (거실 격자 = C2 신뢰도 스펙트럼 2극, paper §7.2 표 3)

| 코드 ID | paper 라벨 | 시나리오 | 환경 | 핵심 검증 대상 | 명세 |
|---|---|---|---|---|---|
| **S5** | S1 | 모호한 referent (식탁 위 동일 머그컵 3개) | 실내 | **C2-a 신뢰도 변조 (RQ2)** — 다후보→저신뢰→넓은 마진 | **[✓](S5-ambiguous-referent/README.md)** |
| **S6** | S2 | 단일 referent (소파) | 실내 | **C2-a 신뢰도 변조 (RQ2)** — 단일후보→고신뢰→좁은 마진 | **[✓](S6-adversarial-setpoint/README.md)** |

> **RQ1·단조성-하한·adversarial setpoint는 시나리오가 아니라 결함 축(하한 검증 격자 = tier1 격리·사용자 직격)이 담당** (ADR-0039 D2). 종전 "S6 = 적대적 목표 지점 / C2 단조성-하한 RQ1" 배정은 폐기.
>
> **디렉터리명 stale 주의**: `S6-adversarial-setpoint`·`S7-cognitive-lapse`는 ADR-0006 시절 명칭으로, path·로그 안정성 위해 유지(ADR-0044 D3 — 코드 ID 디커플). 실제 역할은 위 표·아래 제외 목록 기준.

## paper-1 제외 시나리오 (ADR-0039 D2)

| 코드 ID | 시나리오 | 처리 |
|---|---|---|
| **S3** | 외부 지붕 점검 (실외) | paper-1 제외 — 실외([ADR-0005 D2](../../docs/handover/decisions/0005-paper1-framing.md)) |
| **S7** | 인지 단절 입력 (의자 2개) | **폐기** — S5와 같은 모호 극이라 C2-a 중복 (인지단절 C3는 결함 축이 담당) |
| **S8** | 군중 영상 촬영 (실외) | **paper-2 이관** — 다중 회피영역 미구현·§5 정형은 단일 회피영역 |

## Sanity 시나리오 (회귀 only, paper §8 표 미포함)

`sanity/S1-static-monitoring/`, `sanity/S2-multi-room-call/`, `sanity/S9-failsafe-trigger/` — L2 명세는 메인 5 완료 후.

## 각 시나리오 디렉터리 구조

```
S{n}-{name}/
├── README.md          # L2: 월드·객체·사용자 위치·미션·성공/실패 정의
├── world.sdf          # (구현) Gazebo SDF
├── launch.py          # (구현) ROS 2 launch
└── fault_config.yaml  # (L4) 결함 주입 매개변수
```

L3 (센서·노이즈)·L4 (결함 주입)·L5 (로깅·지표)는 메인 5의 L2가 마무리된 후 일괄 통과.
