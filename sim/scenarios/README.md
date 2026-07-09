# sim/scenarios/

시나리오별 명세·구현 자산. **시나리오 셋 잠금 = [ADR-0006](../../docs/handover/decisions/0006-paper1-scenario-set.md)**.

## 메인 시나리오 (paper §8 결과 표 25행 = 5 시나리오 × 5 intent ablation)

| # | 시나리오 | 환경 | 핵심 검증 대상 | L2 명세 | 구현 |
|---|---|---|---|---|---|
| **S3** | 외부 지붕 점검 | 실외 | drone-specific affordance + T0/T1 통합 | **[✓](S3-roof-inspection/README.md)** | 대기 |
| **S5** | 모호한 referent | 실내 | **C2 신뢰도 변조 (시변 $c(t)$)** | **[✓](S5-ambiguous-referent/README.md)** | 대기 |
| **S6** | 적대적 목표 지점 (adversarial setpoint) | 실내 | **C2 단조성-하한 (RQ1 핵심)** | **[✓](S6-adversarial-setpoint/README.md)** | 대기 |
| **S7** | 인지 단절(cognitive lapse) 입력 | 실내 | 결합제약 인지 측면 + C3 cancel | **[✓](S7-cognitive-lapse/README.md)** | 대기 |
| **S8** | 군중 영상 촬영 | 실외 | 다중 동적 회피 영역 + [ADR-0005 D2](../../docs/handover/decisions/0005-paper1-framing.md) | **[✓](S8-crowd-cinematography/README.md)** | 대기 |

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
