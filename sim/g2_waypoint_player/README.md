# g2_waypoint_player

G2 scripted waypoint player — YAML 시나리오에 정의된 ENU velocity sequence를
시간축 따라 [G1](../g1_offboard/)의 nominal 토픽으로 publish.

**ADR**: [ADR-0011 D1](../../docs/handover/decisions/0011-g1-offboard-interface.md) nominal 토픽 분리 위에 적층.
**역할**: baseline B0의 `$u_\text{nom}$` source. S5·S6·S7 시나리오 재생.

## 토픽 wiring

```
g2_waypoint_player                       (YAML 시퀀스)
  ↓
/cmd/trajectory_setpoint_safe            (TwistStamped, ENU)
  ↓
[g1_offboard: ENU→NED 변환]
  ↓
/fmu/in/trajectory_setpoint              (NED, PX4)
```

티어1 구현 후엔 `/cmd/trajectory_setpoint_nominal`로 publish 변경 (티어1이
가로채 `_safe`로 forward).

## 시나리오

| 짧은 이름 | YAML | 검증 목표 |
|---|---|---|
| `c0` | [c0_up_down_sweep.yaml](scenarios/c0_up_down_sweep.yaml) | z축 ENU→NED 부호 반전 |
| `c1` | [c1_square_pattern.yaml](scenarios/c1_square_pattern.yaml) | xy 평면 ENU↔NED 4축 매핑 |
| `c2` | [c2_s6_adversarial.yaml](scenarios/c2_s6_adversarial.yaml) | S6 적대적 nominal — 티어1 baseline B0 |

## 실행

전제: `./scripts/up.sh` 완료 + G1 ACTIVE.

```bash
./scripts/run_g2_scenario.sh c0     # z축 검증
./scripts/run_g2_scenario.sh c1     # xy 평면 검증
./scripts/run_g2_scenario.sh c2     # S6 baseline
```

시퀀스 완료 시 노드가 자동 종료 (`exit_on_finish=True`). G1은 nominal 끊긴 후
`nominal_timeout_s` (0.5s) 지나면 자동 hover 복귀.

## YAML 스키마

```yaml
name: <str>                    # 시나리오 식별자
description: <str>             # 한 줄 설명
publish_rate_hz: <float>       # default 10
finish_hover_s: <float>        # 마지막 step 후 zero-velocity 유지
steps:
  - duration_s: <float>
    linear: {x: <float>, y: <float>, z: <float>}    # ENU m/s
    angular: {z: <float>}                            # 선택, ENU yaw rate
    note: <str>                                      # 선택, 디버깅
```

새 시나리오 추가는 `scenarios/<name>.yaml` 한 줄 + 빌드(`colcon build`). 별도
코드 변경 불필요.

## 참조

- [ADR-0011](../../docs/handover/decisions/0011-g1-offboard-interface.md) — G1 인터페이스 4결정 + F1~F5.
- [RESEARCH_CONTEXT §B6](../../docs/RESEARCH_CONTEXT.md) — 1차 논문 baseline B0/B1/B2.
- [sim/scenarios/S6-adversarial-setpoint/](../scenarios/S6-adversarial-setpoint/) — c2 시퀀스의 L2 명세.
