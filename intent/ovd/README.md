# intent_ovd

Open-Vocabulary Detection (OVD) wrapper. *의도해석기* 의 **ξ_ovd context 채널** 공급원 ([cmsm-proof §10.1](../../paper/cmsm-proof.md)).

## 잠금 결정 (ADR-0021 1차 답)

- **백본**: YOLO-World 단일 (ultralytics 경유). Grounding DINO 는 paper-1 범위 밖.
- **백엔드**: PyTorch MPS (Apple Silicon arm64).
- **모델 weight**: opt-in. `OVD_FETCH_WEIGHTS=1 scripts/install_ovd.sh` 일 때만 받음.
- **위치**: [`intent/ovd/`](.) ament_python 패키지 — `intent_confidence` 와 동일 패턴.

## 현재 상태

- **B1 완료** (2026-05-25): 환경 + 패키지 + `detector.py` (291 LoC) + `detector_node.py` (208 LoC) + `vocabulary.py` (83 LoC) + `launch/ovd_detector.launch.py` + host 46/1 skip + Docker light smoke 4/4. [ADR-0021](../../docs/handover/decisions/0021-ovd-backbone-lock.md) 백본 잠금.
- **e2e wiring 진입** (2026-05-31): [ADR-0024](../../docs/handover/decisions/0024-ovd-execution-location.md) 측 실행 위치 잠금. 1차 = host venv MPS + FastDDS Discovery Server → **amendment 측 D1 (b) Docker CPU 채택** (host macOS 측 ROS 2 native install 차단요인 발견 후). paper §C sweep 측 *진짜 c 산출 위* strict e2e 길 ([ADR-0005 D4 amendment](../../docs/handover/decisions/0005-paper1-framing.md#amendment-2026-05-31--strict-e2e-길-명시-d4-measurement-scope-정정)) 의 1 단계. 후속 코드 작업 ([ROADMAP §3 B1](../../docs/handover/ROADMAP.md) trail) = Dockerfile 측 ultralytics + torch CPU install + `start_intent_stack.sh` 측 Docker exec intent_ovd launch + `scripts/up.sh` 측 colcon build intent_ovd 추가 + sim camera 토픽 노출 + heavy smoke. [ADR-0024 D4 표 amendment 정정](../../docs/handover/decisions/0024-ovd-execution-location.md#phase-1-plan-정정-d4-표-갱신) 참조.

> **host venv 측 OVD 사용 = unit test 한정** (ADR-0024 amendment 2026-05-31). `intent/ovd/test/` 측 알고리즘 단위 검증 위주. 실 sim 측 추론 = Docker container CPU.

## Layout

```
intent_ovd/
  ├─ detector.py       # YOLO-World wrapper (ultralytics 호출, MPS 디스패치)
  ├─ vocabulary.py     # 어휘 prompt → class label 매핑
  └─ detector_node.py  # ROS 2 노드 — sensor_msgs/Image → vision_msgs/Detection2DArray
launch/
  └─ ovd_detector.launch.py
test/
  ├─ conftest.py
  ├─ test_detector.py  # 헤드리스 (CPU fallback) 단위 테스트
  └─ test_vocabulary.py
```

## 환경 구축

```bash
# 1) 스크립트가 알아서 처리:
#    - Homebrew python@3.11 설치 (없으면)
#    - .venv 가 3.11 이 아니면 backup 후 재생성 (현 .venv = 3.9 → 재생성됨)
#    - requirements-dev + requirements-ovd 설치
#    - intent/ovd editable install
$ ./scripts/install_ovd.sh

# 2) 모델 weight 도 같이 받기 (opt-in, ~300 MB):
$ OVD_FETCH_WEIGHTS=1 ./scripts/install_ovd.sh

# 3) .venv 강제 재생성 (디버깅용):
$ FORCE_VENV_REBUILD=1 ./scripts/install_ovd.sh
```

**Python 버전**: 3.11 계열 (2026-05-25 잠금). ML wheel 가용성 + LTS 안정성 sweet spot.

## 참고

- ROADMAP §3 B1 — 완료
- [ADR-0021](../../docs/handover/decisions/0021-ovd-backbone-lock.md) — OVD 백본 (YOLO-World 단일) 잠금
- [ADR-0024](../../docs/handover/decisions/0024-ovd-execution-location.md) — 실행 위치 1차 잠금 (host venv MPS + FastDDS Discovery Server)
- cmsm-proof §10.1 ξ_ovd context 채널 정의
- cmsm-proof §10.5 (CA-2) OVD 정확성 운용 가정
