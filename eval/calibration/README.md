# `eval/calibration/` — paper §C 진입 0번 PR

[ADR-0025 D1.b](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d1b)
(+ [amendment 4·5·6·8](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md))
의 calibration 절차를 코드로 실현. paper §C 본실험 *전* 1회 실행 → 결과 YAML
저장 → fault_variant 의 Gaussian $\sigma$ mapping 에 사용.

## 책임 범위

- LLM 의 *자연 환각 분포* $\sigma_\text{LLM,nat}$ 사전 측정
- fault_variant `position_noise_gauss_low` / `gauss_med` 의 $\sigma$ 값 결정 입력
- paper §C 부록 보고 표 (백본 별 $\sigma_\text{LLM,nat}$, swap rate, 무관 σ rate) 생성

## 비책임 (다른 곳)

- 실 API key 관리 — `.env` (gitignored) 또는 환경변수 외부 주입
- paper §C 본실험 (fault-injection trial) — `eval/faults/`, `eval/baselines/`, `eval/metrics/`
- 결과 분석 paper 본문 — paper §C 부록 (B11 작업)

## 백본 ([ADR-0025 D1.b amendment 8](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md))

| 백본 | 세대 | 모델 식별자 | 비고 |
|---|---|---|---|
| GPT-4o | 2024 (두 세대 전) | `gpt-4o-2024-05-13` | [ADR-0014 D1 #6](../../docs/handover/decisions/0014-llm-backbone-six-lock.md#d1) |
| GPT-5.5 | 2026 (현세대) | `gpt-5.5` (**잠정** — env var override) | [ADR-0014 D1 #2](../../docs/handover/decisions/0014-llm-backbone-six-lock.md#d1) |

세대 양 끝점 비교 — paper §C 부록의 "환각 분포 세대 변화" narrative.

**⚠️ GPT-5.5 잠정 식별자 alert (PR #82 review C4 amendment)**:

기본 `gpt-5.5` 는 *placeholder* 라 실 OpenAI API 호출 시 `model_not_found` raise.
paper §C 본실험 직전에 정확한 model ID 가 알려지면 env var 로 override:

```sh
export OPENAI_GPT_5_5_MODEL=gpt-5-2026-04-23  # 실 식별자 확인 후
```

`llm_client.resolve_model_id(Backbone.GPT_5_5)` 가 환경변수 우선 → 실 호출
시 정확한 model 사용. GPT-4o 는 stable 이라 env var 영향 없음.

## 비용 예상 (PR #82 review C11 amendment)

paper §C 본실험 시 calibration 측 OpenAI API 비용:

| 차원 | 값 |
|---|---|
| 시나리오 | 4 (S5/S6/S7/S8) |
| 백본 | 2 (GPT-4o + GPT-5.5) |
| Sample | 50 |
| Total 호출 수 | 400 |

추정 비용 (2026-05 기준, paper §C 진입 시 재확인):
- GPT-4o: ~$0.01 / 호출 → 200 × $0.01 = **$2**
- GPT-5.5: ~$0.03–0.05 / 호출 (추정) → 200 × $0.04 = **$8**
- **합계 ~$10** (calibration 1 회)

paper §C 본실험 (5-way ablation × 1,000 trial) 의 cloud LLM 비용은 별 추정 —
[ROADMAP §6 C18](../../docs/handover/ROADMAP.md) calibration 재실행 트리거와
같이 paper §C 진입 시 확인.

## 시나리오 ([ADR-0025 D3 amendment 7](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d3))

paper §C 시뮬 indoor 한정 — S3 (지붕, 실외) 제외:

- S5 모호 referent
- S6 적대 setpoint (정상 prompt 만, 적대 prompt 는 paper §C 측 `adversarial` 채널)
- S7 인지 단절 (정상 prompt 만, E1-E4 변형 X)
- S8 군중 영상 촬영

각 시나리오 당 $N=50$ 정상 prompt 입력 (temperature 0.7 sampling).

## 실행 절차

1. **API key 설정** (한 번):
   ```sh
   export OPENAI_API_KEY=sk-...
   # GPT-5.5 잠정 식별자 — paper §C 진입 시 실 식별자 확인 후 override
   export OPENAI_GPT_5_5_MODEL=gpt-5.5  # 또는 정확한 ID
   ```
2. **의존성**:
   ```sh
   source .venv/bin/activate
   pip install -r requirements-calibration.txt
   ```
3. **측정** (PromptMode 두 갈래, PR #82 review C1·C2 amendment):
   ```sh
   # NATURAL 모드 (default) — LLM 자연 거동 측정. ADR-0025 D1.b honest 정합.
   python -m eval_calibration.measure --backbone gpt-4o --scenario S5 --n 50
   python -m eval_calibration.measure --backbone gpt-5.5 --scenario S5 --n 50 \
       --mode natural

   # STRICT 모드 — paper §C 본실험 측 (catalog 강제). fault-injection 트랙 측.
   python -m eval_calibration.measure --backbone gpt-4o --scenario S5 --n 50 \
       --mode strict

   # ... S6, S7, S8 반복
   ```
   각 실행은 `results/{backbone}_{scenario}_n{N}_{ts}.yaml` 로 저장.

   **모드 선택 기준**:
   - **NATURAL** (default) = `tool_choice='auto'` + 최소 SYSTEM_PROMPT.
     calibration 측 자연 환각 분포 측정. fail-gracefully (function call 회피) →
     `no_call_rate` 별 보고. ADR-0025 D1.c honest narrative 정합.
   - **STRICT** = `tool_choice='required'` + catalog 강제 SYSTEM_PROMPT.
     paper §C 본실험 (B5+ fault-injection) 측 Tier 2 carrier 측정.
4. **분석**:
   ```sh
   python -m eval_calibration.analyze results/
   ```
   `results/calibration_summary.yaml` + `paper_c_appendix_table.md` 생성.

## 본 PR 의 범위 (스캐폴드)

- ✅ 디렉터리 구조 + dataclass + S5 example YAML
- ✅ pure-Python loader + analyzer (mockable, API key 무관)
- ✅ OpenAI client wrapper (lazy import, mock fallback)
- ✅ 단위 테스트 (host venv 통과)
- ❌ S6/S7/S8 시나리오 YAML — 별 PR
- ❌ 실 API 호출 실행 — paper §C 본실험 측 별 단계 (API key + 비용)
- ❌ paper §C 본실험 통합 — `eval/runner.py` (B7)

## 결과 YAML 스키마 (paper §C 부록 보고용)

```yaml
backbone: gpt-4o-2024-05-13
scenario: S5
n_samples: 50
timestamp: 2026-MM-DDTHH:MM:SSZ
sigma_llm_nat:
  position_xyz_cm: <float>       # |θ_LLM - θ_normal| 의 std (cm)
  target_swap_rate: <float>      # 0-1, swap 발생 비율
  unrelated_sigma_rate: <float>  # 0-1 또는 NaN (ambiguous 시나리오)
  no_call_rate: <float>          # 0-1, NATURAL 모드 fail-gracefully (C1 amendment)
samples:
  - prompt: "..."
    sigma: {sigma: "move_to", theta: {position: [..., ..., ...], max_speed: ...}}
    expected_action: {...}
    deltas:
      position_xyz_cm: <float>
      is_swap: <bool>
      is_unrelated: <bool|null>  # null 이면 ambiguous (C3 amendment)
      is_no_call: <bool>         # C1 amendment
  # ... 49 more
```

## 관련 ADR / 문서

- [ADR-0025](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md) D1.b · D1.c · D5 · amendment 4·5·6·7·8
- [ADR-0014 D1](../../docs/handover/decisions/0014-llm-backbone-six-lock.md#d1)
- [ADR-0006](../../docs/handover/decisions/0006-paper1-scenario-set.md) 시나리오 셋
- [ADR-0013 D2](../../docs/handover/decisions/0013-tier2-spec-lock.md#d2) Tier 2 5 스킬 카탈로그
