# scripts/ — 스크립트 인덱스 (어느 작업엔 어느 스크립트)

> **이 표가 단일 정본.** 실험·sim 작업 시 명령을 재구성하지 말고 *여기서 정본 스크립트를
> 찾아* 쓴다. "비슷해 보이는 다른 스크립트"를 끌어오지 말 것 — ⚠️ 표시된 것은 그 용도가
> *아니다*. 새 스크립트를 정본화하면 이 표에 등록 + 대체된 것을 ⚠️/DEPRECATED 로 표시.
> (배경: 잘못된/stale 스크립트 + 섞인 결과 데이터로 반복된 헛수고 — ADR-0041 ·
> [[one-experiment-script-and-run-dirs]]·[[cleanup-means-currency-not-delete]].)

---

## 1. 실험 실행 + 분석 (★ 진입점은 단 하나)

> **실험은 `run_full_experiment.sh` 하나로만 돌린다.** 새 실행 스크립트 작성·`run_grid` 손
> 체이닝·ad-hoc bash 격자 **금지**. 변형(에피소드·모델·범위)은 전부 *파라미터*. 매 실행이
> 격리된 **`results/runs/<날짜시각>__<tag>/`** 에 결과+`manifest.yaml`(git SHA·파라미터)+자동
> 집계를 담는다 → 데이터 안 섞임·provenance 보증·실행/분석 일원화 (ADR-0041).

| 작업 | 명령 | 결과 위치 |
|---|---|---|
| **본 풀런** (전 RQ/기여) | `./scripts/run_full_experiment.sh` | `results/runs/<id>/` (3백본·10ep·양 다리·자동 집계) |
| **적은-ep 검증** (동일 코드 경로) | `EPISODES=2 BACKBONES="gemma-4-e4b llama-3.2-11b-vision" RUN_TAG=smoke ./scripts/run_full_experiment.sh` | `results/runs/<id>/` |
| 계획만 | `DRY_RUN=1 ./scripts/run_full_experiment.sh` | — |
| **분석** | `metrics_aggregator --output-root results/runs/<id> --backbone <bb>` (run 끝에 *자동* 실행됨) | `results/runs/<id>/aggregate_<bb>.{json,md}` |

> ⚠️ **분석은 항상 특정 `results/runs/<id>/` 만 읽는다.** `results/` 통째·옛 디렉터리(`p5_rerun`·
> `track_b` 등)를 무심코 읽지 말 것 — provenance·valid 여부는 [results/README.md](../results/README.md) 참조.

**§8 figure·표 생성 (paper/figures/scripts/ — 분석 도구, 실험 실행 아님):**
- `extract_per_trial.py` — run 디렉터리 bag → `paper/figures/data/per_trial.csv`+`latency.csv`
  (집계기 `compute_record` 동일 경로 + tier2 결정 분해·grounded-구간 c·LLM 호출 지연). 컨테이너(rosbag2) 1회.
- `extract_htraj.py` — 대표 trial h(t) → `paper/figures/data/htraj.csv`. 컨테이너 1회.
- `make_figures.py` — CSV → F1–F5 figure (`paper/figures/out/f{1..5}_*.{pdf,png}`, 탐색/논문 2벌). host venv.
- `make_tables.py` — aggregate json + CSV → T1–T4 md 표 (`paper/figures/data/T{1..4}.md`). host venv.

**내부 부품 (직접 실행 아님):**
- `run_grid.py` — host-driven 격자 엔진(단일 다리). `run_full_experiment.sh` 가 호출. 직접 데이터런 금지.
- `eval-runner-one`(`run_one.py`) — per-trial(run_grid 가 docker exec).
- ⚠️ `eval-runner`(`runner.py run_all`) = *in-container 스모크 전용* (ADR-0030 D5) — 데이터런 금지.
- ⚠️ `experiment_panel.py` = 서브셋 탐색 웹 UI(옛 eval-runner 명령 생성, 현행화 예정) — 데이터런 금지.
- `analyze_bag_confidence.py` — 단일 bag c/s1 진단(보조).

**ollama 백엔드(edge gemma/llama)**: host 네이티브 **:11435** (전체 RAM). ⚠️ `:11434` = docker
`edge_ollama`(메모리 ~7.75GiB)라 gemma 로드 500 OOM → wrapper 가 조용히 ASK_USER fallback
([[p5-backbone-serving-setup]]). 정본 스크립트에 :11435 + chat 프리플라이트 박힘.

---

## 2. sim 라이프사이클

| 작업 | 스크립트 |
|---|---|
| 영속 셸 기동(빌드+SITL+gz+노드) | `up.sh` (`HEADLESS=1 DRONE_CAMERA=1 OVD=1 SIGMA_BRIDGE=1 SCENARIO=livingroom`) |
| 세션 정리 | `down.sh` (`KEEP_CONTAINER=1` 빠른 재가동) |
| trial 간 SITL+gz 리셋 | `sim_reset.sh` (run_grid 가 호출, host) |
| 영속 노드 재구성 | `restart_persistent_node.sh` |
| T1 native SITL (livingroom/yard) | `run_native_sitl_livingroom.sh` / `run_native_sitl_yard.sh` |
| PX4 preflight 파라미터 완화 | `sitl_set_params.sh` |
| PX4 stdin FIFO(로그 폭증 방지) | `lib_px4_stdin.sh` (source용 lib, 세션 53) |

---

## 3. 스모크 검증 (host/sim 정합 빠른 점검 — 데이터런 아님)

`check_g1_smoke.sh` · `check_f_smoke.sh` · `check_ovd_smoke.sh` · `check_ovd_e2e_smoke.sh` ·
`check_tier2_smoke.sh`(+`mock_tier2_intent.py`·`validate_tier2_smoke.py` 하니스).

---

## 4. 의도·음성 스택 (paper-1 시뮬 외 / 라이브 운용)

`start_intent_stack.sh` · `clarification_loop.py`(STT→LLM→ask_user→TTS 루프) ·
`stt_pipeline.py`/`run_stt.sh` · `tts_pipeline.py`/`run_tts.sh` · `teleop.sh`.
런북: [docs/RUN_VOICE_PIPELINE.md](../docs/RUN_VOICE_PIPELINE.md).

---

## 5. 정확도·그라운딩 벤치 (★ 격자 아님 — 별 트랙)

⚠️ 본실험 격자와 **혼동 금지**. LLM 의도/그라운딩 정확도 측정 전용:
`c33_accuracy_bench.py` · `c33_backend_smoke.sh` · `c37_grounding_verify.py`(SSH 터널로 Mac mini ollama).

---

## 6. 모델·자산 빌드 (sim/models 는 .gitignore — 기동 전 1회)

| 자산 | 스크립트 |
|---|---|
| S5 머그컵 3개 (Fuel mesh) | `build_mug.py` (livingroom world 필수 — 없으면 gz world 로드 실패) |
| S8 yard 사람 mesh | `build_yard_people.py` |
| MicroXRCEAgent (macOS) | `build_microxrce_agent_macos.sh` |

---

## 7. 설치/환경

`setup_native_macos.sh` · `install_docker_desktop_macos.sh` · `install_jetson.sh` ·
`install_ovd.sh` · `install_piper.sh` · `install_whisper_cpp.sh`.

---

## 8. 카메라·기타 유틸

`gz_cam_relay_host.py`/`gz_cam_relay_node.py`(카메라 중계) · `grab_cam_frame.py` ·
`run_g2_scenario.sh` · `parse_g2_pos.py`.
