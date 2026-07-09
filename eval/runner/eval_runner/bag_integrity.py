"""rosbag2 bag 무결성 판정 + run 전체 bag_status 집계 스캔.

세션 34 전체 리뷰 P2 후속 ([progress](../../../docs/handover/progress/2026-06-12-full-review-tier1-failsafe.md)
§리뷰 잔여 후속 4) — 종전 `runner.is_trial_complete()` 는 trial_meta.yaml
*존재* 만 확인 → trial 이 시작됐으나 bag 기록 중 실패(프로세스 사망·디스크
등)하면 메트릭 계산 측 빈-입력 ValueError 로 해당 trial 이 결과에서 *조용한
제외(silent drop)* 될 수 있음. paper §C 본실험 1,000 trial 규모에서 조용한
제외는 결과 편향 위험 → trial 종료 시 bag 무결성을 판정해 trial_meta.yaml 의
`bag_status` 필드로 기록하고, resume · 집계 단계가 이를 *명시적으로* 다룬다.

## 판정 기준 (bag_signals / bag_pipeline 의 실 입력 요구와 정합)

| topic | 최소 sample | 근거 |
|---|---|---|
| `/vehicle_local_position` | 1 | `positions_to_h_series` 측 빈 list 거부 |
| `/cmd/trajectory_setpoint_safe` | 2 (B4 reject-all 면제) | `BagInputs` / `extract_loop_periods` 측 n >= 2 강제. 단 B4 에서 게이트가 한 번도 accept 안 했으면(dispatch 0) actuation 부재가 C3 정상 → 면제 |
| `/intent/estimator/report` | 1 (B2/B3/B4 만) | `build_r_series_for_baseline` 측 B2+ 빈 list 거부 |
| `/tier2/decision` | 1 (B4 만) | 게이트 작동(+상류 체인 전이) 보장 — REJECT 도 결정이라 항상 ≥1, 게이트 크래시 검출 |
| 활성 채널 `_faulted` 토픽 | 1 (NONE 제외) | fault *선언됐으나 미주입* 검출 — fault×sigma 비호환 시 injector 조용한 no-op (격자 smoke 2026-06-14, ADR-0025 amendment) |

활성 fault 채널의 `_faulted` 토픽(`FAULT_CHANNEL_FAULTED_TOPIC`)은 launch_composition
이 record 셋에 포함하며, 0 sample 이면 fault transform 이 한 번도 발행 안 된 것
(= 미주입) → 'incomplete'. NONE 채널은 변형 출력이 없으므로 추가 요구 없음.

**B4 게이트 reject 처리** (세션 53 B4 게이트 sim e2e): B4 에서 신뢰도 미달·사양
위반으로 게이트가 σ 를 전부 REJECT/CONFIRM 하면 actuation(SIGMA_FINAL dispatch)이
차단되어 setpoint 가 0 이 된다. 이는 C3 게이트의 *정상 동작*이지 기록 실패가 아니다.
게이트 dispatch 토픽(`/intent/llm_sigma_raw`, ACCEPT 에서만 발행)을 B4 record 셋에
포함해 sample 수로 "게이트가 한 번이라도 accept 했는가"를 판정 — 0 이면 setpoint
요구를 면제(valid-complete), ≥1 이면 actuation 이 흘렀어야 하므로 setpoint ≥2 를
요구(누락 = 진짜 incomplete). 게이트 작동 자체는 `/tier2/decision` ≥1 로 별도 보장.

rosbag2 측 `metadata.yaml` 은 record 프로세스가 *정상 종료* 할 때만 작성됨
(rosbag2 설계) → 부재 = record 비정상 종료 추정 → 'incomplete'.

## 제3 범주 'fault_not_applicable' (ADR-0037 D1 확장 — A′)

gpt-4o × S5(모호) × hallucination 에서 *의도해석기*가 전 호출 ask_user 로
후퇴하면 행동 호출 σ 가 발행되지 않아 `/intent/llm_sigma_raw` 0 sample →
위 결함 무결성 가드가 'incomplete' 판정. 그러나 이는 하니스 결함(injector
no-op)이 아니라 **의도 계층의 정당한 명료화 후퇴로 주입 표면이 닫힌 것**
(주입이 정의되지 않음). 다음 4 조건이 *모두* 성립하면 'incomplete' 대신
'fault_not_applicable' 로 분류한다:

  1. incomplete 사유가 *오직* 활성 fault 채널의 `_faulted` 토픽 미달뿐
     (setpoint 부족 등 다른 사유 동반 시 그대로 incomplete).
  2. 그 faulted 토픽 = dispatch(`/intent/llm_sigma_raw`) 계열 — σ 부재가 원인.
  3. bag 안 σ_raw = 0 이면서 estimator report ≥ 1 (의도 스택 생존 증거).
  4. trial 디렉터리의 LLM TRIAL_LOG JSONL(`*_llm_*.jsonl` — cloud_llm_*.jsonl /
     edge_llm_*.jsonl, run_grid 가 TRIAL_LOG_DIR=trial 디렉터리로 전파)의 모든
     레코드 `skills` 필드가 ask_user 만 포함 — 명료화 후퇴 증거.

JSONL 부재(legacy run)·판독 불가 시 fault_not_applicable 판정 불가 → 기존
incomplete 유지(보수적). 'fault_not_applicable' trial 은 **결함 주입 통계에
산입 금지**(집계 측 별도 보고), **resume 재실행 대상 아님**(결정론적 거동 —
재실행해도 동일한 명료화 후퇴).

## 책임 분리

| 함수 | 시점 | 역할 |
|---|---|---|
| `check_bag_integrity` | trial 종료 직후 (`runner.run_trial`) | bag → 'complete' / 'incomplete' / 'fault_not_applicable' 판정 |
| `scan_trial_bag_statuses` | run 종료 후 · 메트릭 집계 진입 전 | output_root 전체 trial_meta.yaml 의 bag_status 집계 |
| `format_bag_status_scan` | 집계 보고 | incomplete / fault_not_applicable trial 개수 + trial id 명시 출력 |
| `rejudge_trial_bag_statuses` | 기존 run 재분류 (`eval-runner --scan-bags --rejudge-bags`) | 'incomplete' trial 을 bag+JSONL 로 재판정해 trial_meta.yaml 갱신 |

**메트릭 집계(B7 후속 aggregation)는 `scan_trial_bag_statuses` 를 반드시 먼저
호출해 incomplete trial 을 명시 보고한다 — 조용한 제외 금지.** 본 필드 도입
전 legacy trial_meta.yaml (bag_status 부재)은 'unknown' 으로 분류해 별도
보고한다.

※ 재현성(seed 5차원 해시) 로직과 무관 — ADR-0025 측 seed 정책 비접촉.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import yaml

from eval_baselines.schemas import BaselineMode
from eval_faults.fault_scenario import (
    FAULT_CHANNEL_FAULTED_TOPIC,
    FaultChannel,
)


BAG_STATUS_COMPLETE = 'complete'
BAG_STATUS_INCOMPLETE = 'incomplete'
# 제3 범주 (ADR-0037 D1 확장 — A′, 모듈 docstring): 결함 선언됐으나 의도 계층의
# 명료화 후퇴(전 호출 ask_user)로 행동 호출 σ 가 없어 주입이 정의되지 않음.
# 결함 주입 통계 산입 금지(별도 보고) + resume 재실행 대상 아님(결정론적 거동).
BAG_STATUS_FAULT_NOT_APPLICABLE = 'fault_not_applicable'
# 본 필드 도입 전 기록된 legacy trial_meta.yaml (bag_status 키 부재) 의
# read-side 분류 — write side 는 'unknown' 을 절대 쓰지 않는다.
BAG_STATUS_UNKNOWN = 'unknown'

ROSBAG_METADATA_FILENAME = 'metadata.yaml'

_TOPIC_POSITION = '/fmu/out/vehicle_local_position_v1'
_TOPIC_SETPOINT = '/cmd/trajectory_setpoint_safe'
_TOPIC_ESTIMATOR_REPORT = '/intent/estimator/report'
_TOPIC_TIER2_DECISION = '/tier2/decision'
# B4 게이트 accept-dispatch(SIGMA_FINAL) — 게이트는 ACCEPT 에서만 발행
# (gate_node._on_command). 본 토픽 sample 수 = 게이트가 actuation 으로 흘려보낸
# σ 수 → "한 번이라도 accept 했는가" 판정에 사용 (launch_composition 이 B4 record
# 셋에 포함). REJECT/CONFIRM 만 있던 trial 은 0 → actuation·setpoint 부재가 정상.
_TOPIC_DISPATCH = '/intent/llm_sigma_raw'

# LLM TRIAL_LOG JSONL glob (trial 디렉터리) — intent_llm cloud_llm/edge_llm 의
# `_write_trial_log` 산출물(cloud_llm_<model>.jsonl / edge_llm_<tag>.jsonl).
# run_grid.run_one 이 TRIAL_LOG_DIR 을 bag 과 같은 trial 출력 디렉터리로 전파하므로
# check_bag_integrity 의 bag_dir 에서 그대로 glob 가능.
LLM_TRIAL_LOG_GLOB = '*_llm_*.jsonl'
# intent_llm.skill_catalog.SkillName.ASK_USER value — 명료화 후퇴 skill 식별자.
# intent_llm 은 host venv 에 import 불가할 수 있어 enum 대신 문자열 상수로 잠금.
_SKILL_ASK_USER = 'ask_user'


def required_min_counts(
    baseline_mode: BaselineMode,
    fault_channel: Optional[FaultChannel] = None,
    gate_dispatched: bool = True,
) -> Dict[str, int]:
    """baseline · fault 채널 별 필수 토픽 → 최소 message 수 매핑.

    모듈 docstring 측 판정 기준 표 잠금 — bag_signals / bag_pipeline 측 빈-입력
    ValueError 발생 조건의 *사전* 검출. B0/B1a/B1b 측 estimator report 미요구
    (`build_r_series_for_baseline` 측 정적 r — 빈 list OK; ADR-0025 amendment 19).

    **fault 무결성 가드** (ADR-0025 amendment, 격자 smoke 2026-06-14): 활성
    fault 채널(NONE 제외)의 injector ``_faulted`` 출력 토픽을 ≥1 sample 요구.
    fault×sigma 비호환(예: positional variant ↔ inspect σ) 시 injector 가
    ValueError 를 log 후 *조용히 no-op* → 변형 출력 0 sample → fault 가 *선언
    됐으나 미주입*. 본 토픽이 record 셋(launch_composition) 에 포함되므로 0
    sample 이면 'incomplete' 판정 → scan-bags 가 명시 보고(조용한 제외 금지).
    ``fault_channel=None`` 또는 NONE 측 추가 요구 없음(back-compat).

    **B4 게이트 reject 가드** (세션 53 B4 게이트 sim e2e): B4 에서 게이트가 한 번도
    accept(dispatch)하지 않으면(``gate_dispatched=False`` — 전부 REJECT/CONFIRM)
    actuation 이 차단되어 setpoint 가 발행되지 않는다. 이는 C3 게이트의 *정상* 동작
    (신뢰도 미달·사양 위반 σ 차단)이지 기록 실패가 아니므로 setpoint 요구를 면제한다.
    게이트가 한 번이라도 accept 했으면(``gate_dispatched=True``) actuation 이 흘렀어야
    하므로 setpoint ≥2 를 그대로 요구(누락 = 진짜 incomplete). 게이트 자체의 작동은
    ``/tier2/decision`` ≥1 로 별도 보장하므로 게이트 크래시(결정 0)는 여전히 검출된다.

    Args:
        baseline_mode: BaselineMode enum.
        fault_channel: 활성 FaultChannel (None 측 가드 미적용 — 단위 테스트
            back-compat).
        gate_dispatched: B4 에서 게이트가 ≥1 회 accept(dispatch)했는지. False 면
            setpoint 요구를 면제(전부 reject/confirm = C3 정상). B4 외에는 무관
            (default True — back-compat).

    Returns:
        dict — topic name → 최소 message 수.
    """
    counts = {_TOPIC_POSITION: 1}
    # setpoint 통상 ≥2 (loop-period 추출 n≥2). 단 B4 에서 게이트가 한 번도 accept
    # 하지 않았으면 actuation 부재가 C3 정상 동작 → setpoint 요구 면제.
    if not (baseline_mode == BaselineMode.B4 and not gate_dispatched):
        counts[_TOPIC_SETPOINT] = 2
    if baseline_mode not in (BaselineMode.B0, BaselineMode.B1A, BaselineMode.B1B):
        counts[_TOPIC_ESTIMATOR_REPORT] = 1
    # B4(tier2): gate 가 σ 흐름에 인라인 → 결정 ≥1 이 게이트 작동(+상류 체인 전달)
    # 전이 보장 (세션 49 tier2 통합). REJECT 도 결정이므로 항상 ≥1.
    if baseline_mode == BaselineMode.B4:
        counts[_TOPIC_TIER2_DECISION] = 1
    if fault_channel is not None and fault_channel != FaultChannel.NONE:
        faulted_topic = FAULT_CHANNEL_FAULTED_TOPIC.get(fault_channel)
        if faulted_topic is not None:
            # B4+hallucination: faulted=σ actuation(/intent/llm_sigma_raw)인데 gate
            # REJECT 시 무발행(정상 동작)이라 0 가능 → /tier2/decision 이 체인 작동을
            # 전이 보장하므로 σ-raw 요구 생략(false incomplete 회피).
            if not (baseline_mode == BaselineMode.B4
                    and fault_channel == FaultChannel.HALLUCINATION):
                counts[faulted_topic] = 1
    return counts


@dataclass(frozen=True)
class BagIntegrityResult:
    """단일 trial bag 측 무결성 판정 결과.

    Fields:
        status: BAG_STATUS_COMPLETE | BAG_STATUS_INCOMPLETE |
            BAG_STATUS_FAULT_NOT_APPLICABLE.
        reasons: incomplete/fault_not_applicable 사유 목록 (complete 측 빈
            tuple) — trial_meta 에는 status 만 기록, 사유는 runner 측 stdout
            보고용.
    """

    status: str
    reasons: Tuple[str, ...]


def _all_llm_records_ask_user_only(trial_dir: Union[str, Path]) -> bool:
    """trial 디렉터리의 LLM TRIAL_LOG JSONL 이 *전 호출 ask_user 후퇴* 증거인가.

    명료화 후퇴 증거 판별 (모듈 docstring 제3 범주 조건 4): `*_llm_*.jsonl`
    전 파일·전 레코드에서 ``skills``(자기일관성 샘플들)의 **엄격 다수결이
    ask_user** 여야 True. wrapper 의 skill 채택 규칙이 자기일관성 다수결이므로
    판정 기준 = *wrapper 가 그 호출에서 실제 채택한 skill* — 샘플 하나가
    inspect 여도 다수결 ask_user 면 σ 는 발행되지 않는다 (σ_raw=0 은 caller
    조건 3 이 별도 확인).

    보수적 판정 — 다음은 전부 False (fault_not_applicable 판정 불가 →
    caller 측 기존 incomplete 유지):
      - JSONL 부재 (legacy run — TRIAL_LOG_DIR 미전파).
      - 레코드 0 (빈 파일) — 증거 없음.
      - JSON parse 실패 / ``skills`` 키 부재·list 아님.
      - 어느 한 레코드라도 다수결이 ask_user 미달·동수 (σ 미발행의 다른
        원인 가능).
    """
    log_paths = sorted(Path(trial_dir).glob(LLM_TRIAL_LOG_GLOB))
    if not log_paths:
        return False
    n_records = 0
    for log_path in log_paths:
        try:
            lines = log_path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return False
            skills = record.get('skills') if isinstance(record, dict) else None
            if not isinstance(skills, list) or not skills:
                return False
            n_ask = sum(1 for s in skills if s == _SKILL_ASK_USER)
            if n_ask * 2 <= len(skills):  # 엄격 다수결 미달(동수 포함) → 불인정
                return False
            n_records += 1
    return n_records >= 1


def _is_fault_not_applicable(
    bag_dir: Union[str, Path],
    fault_channel: Optional[FaultChannel],
    topic_counts: Dict[str, int],
    shortfall_topics: Tuple[str, ...],
    baseline_mode: Optional[BaselineMode] = None,
) -> bool:
    """incomplete 후보가 제3 범주 'fault_not_applicable' 인가 (단일 판정 지점).

    모듈 docstring 제3 범주 4 조건의 기계 판정 — 전부 성립해야 True:
      1. 미달 토픽이 *오직* 명료화 후퇴 서명뿐 — 활성 fault 채널의 _faulted
         토픽(=dispatch) 하나, 또는 **B4+hallucination 은 ``/tier2/decision``
         하나** (required_min_counts 가 B4 에서 σ-raw 요구를 decision ≥1 로
         대체하는데, 명령이 게이트에 도달하지 않으면 decision 0 도 같은
         후퇴 서명 — 세션 62 gpt-4o 사례).
      2. 후퇴 서명 대상 채널 = dispatch(``/intent/llm_sigma_raw``) 계열.
      3. σ_raw 0 sample + estimator report ≥ 1 (의도 스택 생존 증거).
      4. LLM TRIAL_LOG JSONL 전 호출 다수결 ask_user (명료화 후퇴 증거).
    """
    if fault_channel is None or fault_channel == FaultChannel.NONE:
        return False
    faulted_topic = FAULT_CHANNEL_FAULTED_TOPIC.get(fault_channel)
    if faulted_topic != _TOPIC_DISPATCH:
        return False
    # 조건 1 — 유일 미달이 후퇴 서명(σ-raw, 또는 B4 는 tier2 decision)이어야
    # 하며, 다른 미달 사유(setpoint 부족 등) 동반 시 그대로 incomplete.
    _b4_signature = (
        baseline_mode == BaselineMode.B4
        and fault_channel == FaultChannel.HALLUCINATION
        and shortfall_topics == (_TOPIC_TIER2_DECISION,)
    )
    if shortfall_topics != (faulted_topic,) and not _b4_signature:
        return False
    # 조건 3 — σ_raw = 0 (주입 표면 닫힘) + estimator 생존.
    if topic_counts.get(_TOPIC_DISPATCH, 0) != 0:
        return False
    if topic_counts.get(_TOPIC_ESTIMATOR_REPORT, 0) < 1:
        return False
    # 조건 4 — JSONL 명료화 후퇴 증거 (부재 시 보수적 incomplete).
    return _all_llm_records_ask_user_only(bag_dir)


def find_rosbag_metadata(bag_dir: Union[str, Path]) -> Optional[Path]:
    """bag_dir 측 rosbag2 metadata.yaml 경로 탐색.

    `ros2 bag record -o <output>` 측 output 이 상대 경로(trial_id)라 record
    프로세스의 cwd 에 따라 bag 이 bag_dir *직속* 또는 *1-단계 하위*
    (``<bag_dir>/<trial_id>/``)에 생길 수 있음 → 두 위치 모두 탐색.

    Returns:
        metadata.yaml 경로 — 직속 우선. 하위 탐색 측 정확히 1개일 때만 반환,
        0개 또는 다중(모호)이면 None (caller 측 incomplete 처리).
    """
    direct = Path(bag_dir) / ROSBAG_METADATA_FILENAME
    if direct.is_file():
        return direct
    nested = sorted(Path(bag_dir).glob(f'*/{ROSBAG_METADATA_FILENAME}'))
    if len(nested) == 1:
        return nested[0]
    return None


def check_bag_integrity(
    bag_dir: Union[str, Path],
    baseline_mode: BaselineMode,
    fault_channel: Optional[FaultChannel] = None,
) -> BagIntegrityResult:
    """단일 trial bag → 'complete' / 'incomplete' / 'fault_not_applicable' 판정.

    판정 순서:
      1. rosbag2 metadata.yaml 탐색 — 부재/다중 → incomplete.
      2. metadata.yaml parse — 실패 또는 스키마 불일치 → incomplete.
      3. `required_min_counts(baseline_mode, fault_channel)` 측 토픽별 message
         수 검사 — 미달 토픽 전부 사유로 수집 (활성 fault 채널의 _faulted
         출력 ≥1 포함 — *선언됐으나 미주입* 검출).
      4. 미달이 *오직* dispatch 계열 _faulted 토픽뿐 + σ_raw 0 + estimator
         생존 + JSONL 전 호출 ask_user 증거 → 'fault_not_applicable' (모듈
         docstring 제3 범주, ADR-0037 amend — `_is_fault_not_applicable` 단일
         판정 지점).

    Args:
        bag_dir: trial bag 디렉토리 (= trial_meta.yaml · LLM TRIAL_LOG JSONL
            위치 — run_grid 가 TRIAL_LOG_DIR 을 같은 디렉터리로 전파).
        baseline_mode: trial 측 BaselineMode — 필수 토픽 셋 분기.
        fault_channel: trial 측 활성 FaultChannel — fault 무결성 가드 분기.
            None(또는 NONE) 측 _faulted 토픽 요구 없음.

    Returns:
        BagIntegrityResult — raise 하지 않음 (판정 함수: 어떤 bag 상태든
        status 로 환원, runner 측 trial_meta 기록을 막지 않기 위함).
    """
    meta_path = find_rosbag_metadata(bag_dir)
    if meta_path is None:
        return BagIntegrityResult(
            status=BAG_STATUS_INCOMPLETE,
            reasons=(
                'rosbag2 metadata.yaml 부재 또는 다중 — record 비정상 종료 추정',
            ),
        )

    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f)
        info = raw['rosbag2_bagfile_information']
        topic_counts = {
            entry['topic_metadata']['name']: int(entry['message_count'])
            for entry in info['topics_with_message_count']
        }
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        return BagIntegrityResult(
            status=BAG_STATUS_INCOMPLETE,
            reasons=(f'rosbag2 metadata.yaml parse 실패 — {exc!r}',),
        )

    # B4 게이트가 한 번이라도 accept(dispatch)했는지 — SIGMA_FINAL sample 수.
    # accept 0 이면 actuation·setpoint 부재가 C3 정상 동작이라 setpoint 요구 면제
    # (세션 53 B4 게이트 sim e2e). B4 외에는 무관(required_min_counts 가 무시).
    gate_dispatched = topic_counts.get(_TOPIC_DISPATCH, 0) >= 1

    reasons: List[str] = []
    shortfall_topics: List[str] = []
    for topic, min_count in required_min_counts(
        baseline_mode, fault_channel, gate_dispatched=gate_dispatched,
    ).items():
        got = topic_counts.get(topic, 0)
        if got < min_count:
            reasons.append(f'{topic} 측 message {got} < 최소 {min_count}')
            shortfall_topics.append(topic)
    if reasons:
        # 제3 범주 (ADR-0037 amend): 미달이 오직 dispatch 계열 _faulted 토픽뿐
        # + 명료화 후퇴 증거 → 하니스 결함 아닌 "주입 미정의" — incomplete 로
        # 재실행 루프에 넣지 않는다 (결정론적 거동, 재실행해도 동일).
        if _is_fault_not_applicable(
            bag_dir, fault_channel, topic_counts, tuple(shortfall_topics),
            baseline_mode=baseline_mode,
        ):
            return BagIntegrityResult(
                status=BAG_STATUS_FAULT_NOT_APPLICABLE,
                reasons=(
                    f'{_TOPIC_DISPATCH} 0 sample — 의도 계층의 명료화 후퇴'
                    f'(LLM TRIAL_LOG 전 호출 ask_user)로 행동 호출 σ 미발행 '
                    f'→ 주입 미정의 (fault_not_applicable, ADR-0037 amend)',
                ),
            )
        return BagIntegrityResult(
            status=BAG_STATUS_INCOMPLETE, reasons=tuple(reasons),
        )
    return BagIntegrityResult(status=BAG_STATUS_COMPLETE, reasons=())


# ----------------------------------------------------------------- run 집계 스캔


@dataclass(frozen=True)
class BagStatusScan:
    """run 전체 trial_meta.yaml 측 bag_status 집계 결과.

    Fields:
        complete_ids: bag_status='complete' trial id (정렬).
        incomplete_ids: bag_status='incomplete' *또는* trial_meta.yaml 자체가
            손상(parse 실패·dict 아님)인 trial id — 재실행 대상.
        unknown_ids: legacy trial_meta.yaml (bag_status 키 부재) trial id —
            본 필드 도입 전 기록. 집계 측 별도 명시 보고.
        fault_not_applicable_ids: 제3 범주 (ADR-0037 amend) — 명료화 후퇴로
            주입 미정의. 결함 통계 산입 금지 + 재실행 대상 아님. 집계 측
            별도 카운트·trial id 명시 보고 (조용한 제외 금지).
    """

    complete_ids: Tuple[str, ...]
    incomplete_ids: Tuple[str, ...]
    unknown_ids: Tuple[str, ...]
    fault_not_applicable_ids: Tuple[str, ...] = field(default=())


def scan_trial_bag_statuses(
    output_root: Union[str, Path],
    backbone: str,
) -> BagStatusScan:
    """``<output_root>/<backbone>/*/trial_meta.yaml`` 전수 스캔 → bag_status 집계.

    **메트릭 집계(B7 후속) 진입 전 호출 의무** — incomplete/unknown trial 을
    개수 + trial id 로 명시 보고하기 위한 입력. trial_meta.yaml 이 없는
    디렉토리는 trial 미완(meta 자체 부재 = resume 측 pending)이라 집계 제외.

    Args:
        output_root: RunConfig.output_root.
        backbone: run-level backbone 식별자 (`trial_bag_dir` 경로 정합).

    Returns:
        BagStatusScan — 세 분류 모두 trial_id 정렬.
    """
    root = Path(output_root) / backbone
    complete: List[str] = []
    incomplete: List[str] = []
    unknown: List[str] = []
    fault_not_applicable: List[str] = []
    for meta_path in sorted(root.glob('*/trial_meta.yaml')):
        trial_id = meta_path.parent.name
        try:
            raw = yaml.safe_load(meta_path.read_text(encoding='utf-8'))
        except yaml.YAMLError:
            incomplete.append(trial_id)
            continue
        if not isinstance(raw, dict):
            incomplete.append(trial_id)
            continue
        status = raw.get('bag_status', BAG_STATUS_UNKNOWN)
        if status == BAG_STATUS_COMPLETE:
            complete.append(trial_id)
        elif status == BAG_STATUS_FAULT_NOT_APPLICABLE:
            fault_not_applicable.append(trial_id)
        elif status == BAG_STATUS_UNKNOWN:
            unknown.append(trial_id)
        else:
            incomplete.append(trial_id)
    return BagStatusScan(
        complete_ids=tuple(complete),
        incomplete_ids=tuple(incomplete),
        unknown_ids=tuple(unknown),
        fault_not_applicable_ids=tuple(fault_not_applicable),
    )


def format_bag_status_scan(scan: BagStatusScan, preview_n: int = 20) -> str:
    """BagStatusScan → 사람용 명시 보고 문자열 (조용한 제외 금지).

    incomplete / unknown / fault_not_applicable trial 은 개수 + trial id 를
    출력 (preview_n 초과 시 생략 표시 — 생략도 개수로 명시).
    """
    total = (
        len(scan.complete_ids) + len(scan.incomplete_ids)
        + len(scan.unknown_ids) + len(scan.fault_not_applicable_ids)
    )
    lines = [
        f'bag_status 집계 — 총 {total} trial: '
        f'complete {len(scan.complete_ids)} · '
        f'incomplete {len(scan.incomplete_ids)} · '
        f'fault_not_applicable {len(scan.fault_not_applicable_ids)} · '
        f'unknown {len(scan.unknown_ids)}',
    ]

    def _append_ids(label: str, ids: Tuple[str, ...]) -> None:
        lines.append(label)
        for tid in ids[:preview_n]:
            lines.append(f'  - {tid}')
        if len(ids) > preview_n:
            lines.append(f'  ... (+{len(ids) - preview_n} trial 생략)')

    if scan.incomplete_ids:
        _append_ids(
            f'⚠ incomplete {len(scan.incomplete_ids)} trial — 재실행 대상 '
            f'(`--resume` 측 자동 재실행, 집계 제외 시 반드시 본 목록 보고):',
            scan.incomplete_ids,
        )
    if scan.fault_not_applicable_ids:
        _append_ids(
            f'ℹ fault_not_applicable {len(scan.fault_not_applicable_ids)} trial '
            f'— 명료화 후퇴로 주입 미정의, 결함 통계 제외(ADR-0037 amend). '
            f'재실행 대상 아님(결정론적 거동):',
            scan.fault_not_applicable_ids,
        )
    if scan.unknown_ids:
        _append_ids(
            f'⚠ unknown {len(scan.unknown_ids)} trial — legacy trial_meta.yaml '
            f'(bag_status 부재, 본 필드 도입 전 기록). 무결성 미보장:',
            scan.unknown_ids,
        )
    return '\n'.join(lines)


# ----------------------------------------------------------------- 재분류 (rejudge)


def rejudge_trial_bag_statuses(
    output_root: Union[str, Path],
    backbone: str,
) -> Tuple[Tuple[str, str, str], ...]:
    """기존 'incomplete' trial 을 bag+JSONL 로 *재판정*해 trial_meta.yaml 갱신.

    제3 범주 'fault_not_applicable' 도입(ADR-0037 amend) *이전* 에 기록된
    trial_meta.yaml 재분류 경로 — `--scan-bags` 는 읽기 전용이므로 본 함수가
    유일한 갱신 지점 (`eval-runner --scan-bags --rejudge-bags`, 새 스크립트
    금지 — ADR-0041 D1 기존 CLI 확장).

    범위는 bag_status='incomplete' trial 로 한정 — 판정은 bag(불변)·JSONL
    (불변) 기반 결정론이라 idempotent 하며, complete trial 강등 위험을 만들지
    않는다. 재판정 결과가 달라진 trial 만 bag_status 필드를 in-place 갱신
    (다른 키는 보존, write side 와 같은 block style·sort_keys).

    Args:
        output_root: RunConfig.output_root.
        backbone: run-level backbone 식별자.

    Returns:
        변경 tuple 목록 — (trial_id, 이전 status, 새 status). 무변경 trial 미포함.
    """
    root = Path(output_root) / backbone
    changes: List[Tuple[str, str, str]] = []
    for meta_path in sorted(root.glob('*/trial_meta.yaml')):
        trial_id = meta_path.parent.name
        try:
            raw = yaml.safe_load(meta_path.read_text(encoding='utf-8'))
        except yaml.YAMLError:
            continue  # meta 손상 — scan 이 incomplete 로 보고 (재실행 경로)
        if not isinstance(raw, dict):
            continue
        if raw.get('bag_status') != BAG_STATUS_INCOMPLETE:
            continue
        try:
            # trial_meta 는 uppercase 이름('B2') — BaselineMode[이름] 조회.
            mode = BaselineMode[str(raw['baseline'])]
            channel = FaultChannel(str(raw.get('fault_class', 'none')))
        except (KeyError, ValueError):
            continue  # 좌표 판독 불가 — 보수적으로 미변경
        result = check_bag_integrity(meta_path.parent, mode, channel)
        if result.status == BAG_STATUS_INCOMPLETE:
            continue
        raw['bag_status'] = result.status
        with open(meta_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(raw, f, default_flow_style=False, sort_keys=True)
        changes.append((trial_id, BAG_STATUS_INCOMPLETE, result.status))
    return tuple(changes)
