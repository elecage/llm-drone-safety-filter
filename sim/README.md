# sim/

Simulation assets and launch glue. **No safety logic, no LLM logic.**

Stack (locked, see [CLAUDE.md §A4](../CLAUDE.md)): Gazebo Harmonic + ROS 2 Humble + PX4 main on Ubuntu 22.04 (JetPack 6.2.2, ARM64).

## Layout

- `worlds/` — 공유 Gazebo SDF 조각(방 geometry, 가구 모델 등).
- `models/` — 커스텀 메시/SDF 모델 (드론, 사용자 좌표 프레임 마커, 사람 placeholder 등).
- `launch/` — 공유 launch 유틸리티.
- `scenarios/` — 시나리오별 디렉터리. **시나리오 셋은 [ADR-0006](../docs/handover/decisions/0006-paper1-scenario-set.md)에서 잠금**: 메인 5 (S3·S5·S6·S7·S8) + sanity 3 (S1·S2·S9). 각 시나리오 디렉터리는 자체 `README.md`(L2 명세) + `world.sdf` + `launch.py` + `fault_config.yaml`을 포함.

## 사용

```bash
ros2 launch sim minimal.launch.py        # GUI
ros2 launch sim minimal.launch.py headless:=true   # 회귀/평가
```

설치는 [docs/SETUP_JETSON.md](../docs/SETUP_JETSON.md) 참조.
