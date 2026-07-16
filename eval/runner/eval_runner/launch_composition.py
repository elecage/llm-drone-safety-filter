"""ROS 2 launch composition logic — BaselineConfig → list[NodeSpec].

[B7 #12 분할 2a](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
— host venv 측 *pure-Python* launch composition. ROS 2 launch system 측 import
없이 *node 합성 사양* 만 잠금. 실 launch_description 측 ROS 2 launch_ros·
launch.actions 합성은 후속 PR (분할 2c) 측.

본 모듈 = TrialSpec → list[NodeSpec] *결정론적* mapping. 단위 test 측 6 baseline
별 정확한 node count + identity + parameter wiring 검증. paper §C trial 측 launch
description 측 *재현성 + monitor-ability* 핵심.

## 6 baseline 별 합성 (ADR-0025 amendment 19 — B1→B1a/B1b)

트리 8-노드 기준(rosbag2 + 발화 publisher 포함). 발화 publisher 는 ADR-0030 F5 로
모든 baseline 에 +1(상수) 추가 — 차이 불변, 절대 count 만 +1.

| baseline | tier1 | context_graph | intent_llm | tier2_gate | estimator | injector | rosbag2 | utterance | count |
|---|---|---|---|---|---|---|---|---|---|
| B0  | mode='b0'     | — | mode='direct' | — | ✓ | ✓ | ✓ | ✓ | 6 |
| B1a | mode='b1'     | — | mode='direct' | — | ✓ | ✓ | ✓ | ✓ | 6 |
| B1b | mode='b1_max' | — | mode='direct' | — | ✓ | ✓ | ✓ | ✓ | 6 |
| B2  | mode='b2'     | — | mode='direct' | — | ✓ | ✓ | ✓ | ✓ | 6 |
| B3  | mode='b2'     | ✓ | mode='fusion' | — | ✓ | ✓ | ✓ | ✓ | 7 |
| B4  | mode='b2'     | ✓ | mode='fusion' | ✓ | ✓ | ✓ | ✓ | ✓ | 8 |

ablation chain 정합 (발화 publisher 는 상수라 *차이* 불변):
  - B0→B1a: tier1 mode parameter 차이 (node count 동일 6).
  - B1a→B1b: tier1 mode parameter 차이 b1→b1_max (node count 동일 6).
  - B1b→B2: tier1 mode parameter 차이 b1_max→b2 (node count 동일 6).
  - B2→B3: + context_graph node + intent_llm mode parameter 변경 (6→7).
  - B3→B4: + tier2_gate node (7→8).

## 참조 노드 구현 상태 (모두 구현 완료)

  - `tier1_filter.filter_node` ✓ (safety/tier1/)
  - `intent_confidence.estimator_node` ✓ (intent/confidence/)
  - `eval_faults.injector_node` ✓ (eval/faults/)
  - `intent_llm.wrapper_node` ✓ (intent/llm/, ROADMAP C36 #1)
  - `intent_context.context_graph_publisher` ✓ (intent/context/, ROADMAP C36 #2)
  - `tier2_gate.gate_node` ✓ (safety/tier2_gate/, A4)
  - `rosbag2` 측 launch 측 `ExecuteProcess('ros2 bag record ...')` 측 표현 — node
    아닌 process. 본 NodeSpec 측 동등 spec wraps (kind='process').

본 NodeSpec 측 *kind* 필드 측 ROS 2 launch action 측면 분류 (node | process).
실 launch object 빌드 + 무인 실행은 runner.py (C36 #3/#4 sim 라이프사이클) 측.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple

from eval_faults.fault_scenario import (
    FAULT_CHANNEL_FAULTED_TOPIC,
    FaultChannel,
)
from eval_faults.schemas import FaultVariant

from eval_runner.grid import resolve_fault_scenario_paths
from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec

# 시나리오별 사용자 발화 단일 소스 (scenario_params) — per-trial 발화 publisher 입력
# (ADR-0030 F5). intent 패키지는 scenario_params 를 import 하지 않으나(C1), 본 trial
# 합성(eval_runner)은 tier1_cbf_params 와 동일하게 참조 가능.
from scenario_params.params import scenario_utterance

# ADR-0020 D9 — 변화율 제한기는 추정기 단일. estimator 가 시나리오별 dot_c_max
# (ADR-0023 파생)를 적용하도록 launch_composition 이 중개 전달한다. intent 패키지는
# scenario_params 를 직접 import 하지 않는다(C1 독립성). 단일 진실 소스 =
# scenario_params.params (panel.py 와 동일 경로, conftest 가 sys.path 보장).
from scenario_params.params import tier1_cbf_params

# tier2 gate 정적 사양(geofence/known/dock) 도출용 — 시나리오 장면(scene.py) +
# spawn(local ENU 변환). gate 가 σ.theta 타깃을 이 경계·카탈로그로 검증(C3).
import json as _json
from scenario_params.params import scenario_location, spawn_params, user_marker_params
from scenario_params.scene import scene_objects_for_location

# tier2 geofence (local ENU, spawn 상대) per location — tier0/운용 경계 근사.
# 정상 작업 타깃(소파/의자/사람)은 안, 경계 밖 위험 타깃은 reject. world 방 경계
# − spawn offset (margin 포함). 실측 튜닝은 sim 검증(세션 49 후속).
_GATE_GEOFENCE_BY_LOCATION: Dict[str, Tuple[float, float, float, float, float, float]] = {
    'livingroom': (-3.5, 2.5, -1.5, 2.5, 0.0, 2.4),
    'yard': (-10.0, 10.0, -4.0, 8.0, 0.0, 4.0),
}


def _gate_scenario_params(scenario_id: str) -> Dict[str, Any]:
    """tier2 gate geofence/known/target_poses/dock 를 시나리오 장면에서 도출.

    known_objects = scene 객체 name + ovd_class (gate 미지객체 사양). target_poses·
    dock 은 local ENU(world − spawn). geofence 는 location 별 근사 경계. 모두
    *정적 사양* 이라 센서 토픽 없이 gate 의 geofence·미지객체·신뢰도·자기수정
    사양이 작동(C3). battery/link 사양만 센서 부재로 trivial pass.
    """
    loc = scenario_location(scenario_id)
    sp = spawn_params(loc)
    sx, sy, sz = sp['spawn_x'], sp['spawn_y'], sp['spawn_z']
    known: set = set()
    target_poses: Dict[str, list] = {}
    dock_local = [0.0, 0.0, 0.0]
    for obj in scene_objects_for_location(loc):
        name = str(obj['name'])
        w = obj['position']
        local = [w[0] - sx, w[1] - sy, w[2] - sz]
        known.add(name)
        if obj.get('ovd_class'):
            known.add(str(obj['ovd_class']))
        target_poses[name] = local
        if name == 'dock':
            dock_local = local
    gf = _GATE_GEOFENCE_BY_LOCATION[loc]
    return {
        'geofence_xmin': gf[0], 'geofence_xmax': gf[1],
        'geofence_ymin': gf[2], 'geofence_ymax': gf[3],
        'geofence_zmin': gf[4], 'geofence_zmax': gf[5],
        'known_objects_json': _json.dumps(sorted(known)),
        'target_poses_json': _json.dumps(target_poses),
        'dock_pos_json': _json.dumps(dock_local),
    }


VALID_NODE_KINDS: Tuple[str, ...] = ('node', 'process')


@dataclass(frozen=True)
class NodeSpec:
    """단일 ROS 2 launch action 측 사양 — node 또는 process.

    Attributes
    ----------
    package : str
        ROS 2 package 명 (예: 'tier1_filter', 'intent_confidence', 'eval_faults').
        process kind 측 빈 문자열 또는 'ros2' (rosbag2 측).
    executable : str
        package 측 console_scripts 측 entry name (node kind) 또는 process command
        (process kind 측 'bag record').
    name : str
        ROS 2 node 명 (unique per launch). process kind 측 식별자.
    kind : str
        'node' | 'process'. 'node' 측 launch_ros.actions.Node 매핑, 'process'
        측 launch.actions.ExecuteProcess 매핑.
    parameters : Mapping[str, Any]
        node 측 ROS 2 parameter dict (mode·scenario·seed 등). process 측 CLI
        arguments dict.
    """

    package: str
    executable: str
    name: str
    kind: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in VALID_NODE_KINDS:
            raise ValueError(
                f'kind={self.kind!r} 무효 — {VALID_NODE_KINDS} 중 하나여야 함'
            )
        if not self.executable.strip():
            raise ValueError('executable 빈 문자열 불가')
        if not self.name.strip():
            raise ValueError('name 빈 문자열 불가')


# 노드 이름 잠금 — 본 launch composition 측 ROS 2 namespace 측 *unique* 보장.
# 본 이름 측 trial 측면 globally unique 측 ros2 launch 측 conflict 회피.
NODE_NAME_TIER1 = 'tier1_filter'
NODE_NAME_INTENT_LLM = 'intent_llm_wrapper'
NODE_NAME_CONTEXT_GRAPH = 'intent_context_graph'
NODE_NAME_TIER2_GATE = 'tier2_gate'
NODE_NAME_ESTIMATOR = 'intent_confidence_estimator'
NODE_NAME_CONF_PUBLISHER = 'intent_confidence_publisher'
NODE_NAME_INJECTOR = 'fault_injector'

# ADR-0050 D7 안 B — 합성 신뢰도 격리에서 publisher_node → estimator external
# 사이의 raw c 토픽. estimator external_c_topic 기본값과 정합.
SYNTHETIC_C_TOPIC = '/intent/c_synthetic_raw'
NODE_NAME_ROSBAG = 'rosbag2_record'
NODE_NAME_UTTERANCE = 'trial_utterance_pub'

# per-trial 발화 publisher 발행 횟수·주기 — wrapper 구독 establish 전 race 회피 +
# σ latch(블로커 2a)라 다회 발행도 동일 σ. --times N --rate R 로 N회 후 종료.
_UTTERANCE_TIMES = 5
_UTTERANCE_RATE_HZ = 1.0
# 발화 raw 토픽 — wrapper 기본 구독(adversarial 채널은 injector 가 raw→_faulted 변환,
# wrapper 가 _faulted 구독; publisher 는 항상 raw 로 발행).
UTTERANCE_RAW_TOPIC = '/intent/user_prompt_raw'

# backbone = run-level 파라미터 (격자 차원 아님 — ADR-0025 D3 5-dim 유지,
# ADR-0014 D5 backbone ablation 은 backbone 별 run 반복). runner.RunConfig 가
# 명시 override; 본 default 는 rosbag_node_spec / 단위 test 측 backbone-무관
# 경로용 (intent_llm.registry 9 식별자 중 로컬 기본). 실 등록 검증은 wrapper_node.
DEFAULT_BACKBONE = 'gemma-4-e4b'


def compose_trial_node_specs(
    trial: TrialSpec,
    backbone: str = DEFAULT_BACKBONE,
) -> List[NodeSpec]:
    """TrialSpec → list[NodeSpec] *결정론적* 합성.

    합성 순서 (launch 측 start order 측 의도):
      1. tier1_filter (안전 계층 측 *먼저 ready* 후 intent layer 측 LLM σ 수신)
      2. context_graph (선택) — intent_llm wrapper 측 fusion 입력 준비
      3. intent_llm wrapper — LLM σ_raw publish
      4. tier2_gate (선택) — σ_raw → σ_gated 측 시간논리 사양 적용
      5. intent_confidence_estimator — c 추정
      6. fault_injector — fault hook 시작
      7. rosbag2_record — 모든 topic record (downstream 구독 establish)
      8. trial_utterance_pub — 사용자 발화 N회 발행 (recorder 다음, 사슬 구동)

    Args:
        trial: eval_runner.schemas.TrialSpec.
        backbone: intent_llm wrapper 측 registry 식별자 (run-level 파라미터).
            default = DEFAULT_BACKBONE. runner 측 --backbone override.

    Returns:
        list[NodeSpec] — len 6/6/6/6/7/8 (B0/B1a/B1b/B2/B3/B4).
    """
    config = trial.baseline_config
    specs: List[NodeSpec] = []
    fault_channel = trial.fault_scenario.channel

    # ── σ 파이프라인 체인 (세션 49 — tier2 인라인 통합 + hallucination 인라인) ──
    # wrapper → [injector if hallucination] → [gate if tier2] → SIGMA_FINAL
    # (=/intent/llm_sigma_raw, actuation: sigma_bridge + estimator 가 읽음). 각 인라인
    # 스테이지가 상류 σ 를 받아 변형(injector)/검증(gate) 후 하류로 전달. tier2 gate
    # 를 σ 흐름에 *직렬* 화 → REJECT 시 actuation 차단(C3 충실, B4≠B3). hallucination
    # σ 변형도 actuation 도달 → 위험 swap 이 실제 비행되어 필터 시험(RQ1).
    SIGMA_FINAL = '/intent/llm_sigma_raw'
    _sigma_stages: List[str] = []
    if fault_channel == FaultChannel.HALLUCINATION:
        _sigma_stages.append('injector')
    if config.tier2_enabled:
        _sigma_stages.append('gate')
    if _sigma_stages:
        _chain = [
            f'/intent/llm_sigma_chain{i}' for i in range(len(_sigma_stages))
        ] + [SIGMA_FINAL]
    else:
        _chain = [SIGMA_FINAL]
    _wrapper_out = _chain[0]
    _stage_io = {
        s: (_chain[i], _chain[i + 1]) for i, s in enumerate(_sigma_stages)
    }

    # 1. tier1_filter — 항상. brake_buffer_m 은 ADR-0050 D2 제동 버퍼 실험 파라미터
    # (기본 0.0 = off, 기존 거동·정리 불변). 환경변수 TIER1_BRAKE_BUFFER_M 로만 켜서
    # 격리 검증 격자에서 CBF-ZOH overshoot 흡수를 시험한다(본 격자 밖 실험은 미설정).
    tier1_params: Dict[str, Any] = {
        'mode': config.tier1_mode,
        'scenario': trial.scenario_id,
    }
    _brake_buffer = os.environ.get('TIER1_BRAKE_BUFFER_M')
    if _brake_buffer:
        tier1_params['brake_buffer_m'] = float(_brake_buffer)
    specs.append(NodeSpec(
        package='tier1_filter',
        executable='filter_node',
        name=NODE_NAME_TIER1,
        kind='node',
        parameters=tier1_params,
    ))

    # 2. context_graph (선택) — context_aug=True 측만.
    if config.context_aug:
        specs.append(NodeSpec(
            package='intent_context',
            executable='context_graph_publisher',
            name=NODE_NAME_CONTEXT_GRAPH,
            kind='node',
            parameters={
                'scenario': trial.scenario_id,
            },
        ))

    # 3. intent_llm wrapper — 항상. mode 차이 = context_aug=True 측 'fusion',
    # False 측 'direct'. backbone = run-level 파라미터 (registry 식별자).
    # ADR-0029 D-A4 fault remap = parameters override: adversarial 채널 활성 시
    # 소비 토픽(utterance_topic)을 injector 의 _faulted 출력으로 가리킨다. wrapper
    # docstring 이 명시한 의도된 패턴 — launch_ros remappings 대신 ROS 파라미터.
    wrapper_params: Dict[str, Any] = {
        'mode': 'fusion' if config.context_aug else 'direct',
        'scenario': trial.scenario_id,
        'backbone': backbone,
        # σ 체인 첫 토픽으로 출력 (인라인 스테이지 없으면 SIGMA_FINAL).
        'output_topic': _wrapper_out,
    }
    if fault_channel == FaultChannel.ADVERSARIAL:
        wrapper_params['utterance_topic'] = FAULT_CHANNEL_FAULTED_TOPIC[
            FaultChannel.ADVERSARIAL
        ]
    specs.append(NodeSpec(
        package='intent_llm',
        executable='wrapper_node',
        name=NODE_NAME_INTENT_LLM,
        kind='node',
        parameters=wrapper_params,
    ))

    # 4. tier2_gate (선택) — tier2_enabled=True(B4) 측만. 세션 49: σ 흐름에 인라인
    # 직렬화 — command(상류 σ) → accept 시 dispatch(=SIGMA_FINAL actuation),
    # decision(=/tier2/decision, eval ARS/QR 정합). geofence/known/dock 는 시나리오
    # 장면에서 도출(정적 사양 — 센서 토픽 없이 geofence·미지객체·신뢰도·자기수정
    # 사양 작동). REJECT 시 SIGMA_FINAL 무발행 → actuation 차단(C3).
    if config.tier2_enabled:
        gate_in, gate_out = _stage_io['gate']
        gate_params: Dict[str, Any] = {
            'scenario': trial.scenario_id,
            'command_topic': gate_in,
            'dispatch_topic': gate_out,
            'decision_topic': '/tier2/decision',
        }
        gate_params.update(_gate_scenario_params(trial.scenario_id))
        specs.append(NodeSpec(
            package='tier2_gate',
            executable='gate_node',
            name=NODE_NAME_TIER2_GATE,
            kind='node',
            parameters=gate_params,
        ))

    # 5. intent_confidence_estimator — 항상. ADR-0020 D9: 변화율 제한기는 추정기
    # 단일 → estimator 가 시나리오별 dot_c_max(ADR-0023 파생, filter_node 와 동일
    # 값)를 적용하도록 명시 전달. scenario_id 가 S5/S6 이 아닌 manual/smoke 경로면
    # 생략 → estimator sentinel(-1.0) fallback.
    # paper §7.6 정본 — 본실험 신뢰도는 *검출기 출력에 추정기를 적용하여 산출*
    # (estimator_mode='live': OVD detections→s1, LLM σ→s2/s3). synthesis 는 단위·
    # 격리 검증 도구일 뿐 본실험 c 출처가 아니다(ADR-0029 D1). 종전엔 estimator_mode
    # 미전달 → 기본 synthesis 인데 scenario_file 도 미전달 → init 크래시였음(P4-2 발견).
    # ADR-0050 D7 안 B — confidence_source='synthetic:<profile>' 이면 estimator 를
    # external 모드로 두고 publisher_node 가 raw c 프로파일을 SYNTHETIC_C_TOPIC 로
    # 발행 → estimator 가 rate_limit_step 만 적용해 재발행. 변화율 제한기를 estimator
    # 단일(ADR-0020 D9)로 유지해 배포 토폴로지(estimator→tier1) 보존(Figure 1 정합).
    is_synthetic_c = trial.confidence_source.startswith('synthetic:')
    estimator_params: Dict[str, Any] = {
        'scenario': trial.scenario_id,
        'estimator_mode': 'external' if is_synthetic_c else 'live',
    }
    if trial.scenario_id in VALID_SCENARIO_IDS:
        estimator_params['dot_c_max'] = tier1_cbf_params(trial.scenario_id)['dot_c_max']
    if is_synthetic_c:
        # external 모드: raw c 를 publisher 에서 구독. live 전용 remap(ovd·sigma)은
        # 신호 합성이 없으므로 미적용.
        estimator_params['external_c_topic'] = SYNTHETIC_C_TOPIC
        profile = trial.confidence_source[len('synthetic:'):]
        specs.append(NodeSpec(
            package='intent_confidence',
            executable='publisher_node',
            name=NODE_NAME_CONF_PUBLISHER,
            kind='node',
            parameters={
                'scenario_file': f'{profile}.yaml',
                'output_topic': SYNTHETIC_C_TOPIC,
            },
        ))
    else:
        # ADR-0029 D-A4 fault remap. attribute_mismatch → s1 source(ovd detections)는
        # estimator 토픽 override. hallucination 은 세션 49 인라인화로 σ 가 SIGMA_FINAL
        # (=estimator 기본 sigma_raw_topic /intent/llm_sigma_raw)에 실리므로 remap 불요
        # (체인 최종 = 변형된 σ). estimator 는 actuation σ(체인 최종)를 그대로 소비.
        if fault_channel == FaultChannel.ATTRIBUTE_MISMATCH:
            estimator_params['ovd_detection_topic'] = FAULT_CHANNEL_FAULTED_TOPIC[
                FaultChannel.ATTRIBUTE_MISMATCH
            ]
        # B4 c-배선 정정(2026-06-22, ADR-0025 amendment): tier2 활성 시 estimator 가 *게이트
        # 입력*(pre-gate σ)을 읽어야 c 가 게이트 결정 시점에 가용하다. 게이트 출력
        # (SIGMA_FINAL)은 accept 시만 발행되므로 estimator 가 그걸 읽으면 c↔게이트 순환
        # (게이트 c 미수신→reject→SIGMA_FINAL 무발행→estimator 무입력). 비-tier2 는
        # SIGMA_FINAL(=actuation σ) 기본값 그대로(불변). 게이트 입력 = _stage_io['gate'][0].
        if config.tier2_enabled:
            estimator_params['sigma_raw_topic'] = _stage_io['gate'][0]
    # cognitive_lapse(→ /intent/lapse_event) 소비자(wrapper/tier2) wiring 은 미구현
    # (ADR-0029 D-A4 별 항목) — injector 가 이벤트를 발행하나 현재 소비 노드 없음.
    specs.append(NodeSpec(
        package='intent_confidence',
        executable='estimator_node',
        name=NODE_NAME_ESTIMATOR,
        kind='node',
        parameters=estimator_params,
    ))

    # 6. fault_injector — 항상. ROADMAP C25 — trial.seed(5차원 derive)를 injector
    # rng 입력으로. **파라미터 키 = 'seed'** — injector_node 가 declare/read 하는
    # 이름과 일치해야 함(injector.launch.py·CLI `seed:=` 도 'seed'). 종전 'trial_seed'
    # 는 injector 가 안 읽어 scenario.seed(YAML 42)로 fallback → per-trial seed
    # 미적용 버그였음(2026-06-12 C25 점검에서 발견·수정).
    # injector_node 는 `scenario_file`(fault YAML 절대 경로) + `seed` 만 선언·사용
    # (channel·variant 등은 YAML 에서 로드) — 종전 'fault_scenario_name'·'fault_channel'
    # 은 노드가 무시해 모든 trial 에서 injector 가 `scenario_file 필수` 로 사망했음
    # (ADR-0030 F10, 세션 46 실 sim 발견). 경로는 fault name → resolve(EVAL_FAULTS_ROOT
    # 정합, 컨테이너 /workspace/eval/faults).
    try:
        fault_yaml_path = str(resolve_fault_scenario_paths([trial.fault_scenario.name])[0])
    except ValueError:
        # 합성 fault(단위 test fixture) — default registry 미등록. injector 가 실행
        # 되지 않는 host 경로라 placeholder 경로로 충분(실 격자의 5 fault 는 등록되어
        # 절대 경로 해석). 실 trial 에서 미등록 name 이 오면 injector 가 파일 부재로
        # 즉시 실패해 드러난다.
        fault_yaml_path = f'{trial.fault_scenario.name}.yaml'
    injector_params: Dict[str, Any] = {
        'scenario_file': fault_yaml_path,
        'seed': trial.seed,
    }
    if fault_channel == FaultChannel.HALLUCINATION:
        # σ 체인: in=상류(wrapper) σ, out=하류(tier2 활성 시 gate command, 아니면
        # SIGMA_FINAL actuation). _stage_io 가 체인 위치에 맞게 결정.
        injector_params['sigma_in_topic'], injector_params['sigma_out_topic'] = (
            _stage_io['injector']
        )
    # amendment 20 (Track B) — 사용자 지향 적대 변형은 시나리오별 실제 사용자 *world*
    # 위치(scenario_params 단일 출처)를 injector 에 주입 → fault context user_position
    # override(D-T3). fault YAML 좌표 중복(stale 위험) 회피. 변형이 inspect σ 도
    # move_to(user_position) 로 치환하므로 채널은 hallucination·스킬 무관.
    if trial.fault_scenario.variant == FaultVariant.POSITION_WORST_USER_DIRECT.value:
        _um = user_marker_params(scenario_location(trial.scenario_id))
        injector_params['user_position_world'] = [
            _um['user_x'], _um['user_y'], _um['user_z'],
        ]
    specs.append(NodeSpec(
        package='eval_faults',
        executable='injector_node',
        name=NODE_NAME_INJECTOR,
        kind='node',
        parameters=injector_params,
    ))

    # 7. rosbag2_record — 항상. trial.trial_id 측 bag 디렉토리 명.
    # ADR-0025 D4 잠금 토픽 셋 — bag_signals helpers 측 정합 입력.
    record_topics: List[str] = [
        # PX4 uXRCE-DDS 실 발행 토픽 직접 record (ADR-0025 D4 amend, P4-1).
        # 종전 계약명 `/vehicle_local_position` 은 발행자가 없어 bag 무기록이었음.
        '/fmu/out/vehicle_local_position_v1',
        '/cmd/trajectory_setpoint_safe',
        '/intent/grounding_confidence',
        '/intent/estimator/report',
        '/tier2/decision',
        '/clock',
    ]
    # 무결성 가드 (ADR-0025 amendment, 격자 smoke 2026-06-14): 활성 fault 채널의
    # injector _faulted 출력 토픽을 함께 record → bag_integrity 가 ≥1 sample 을
    # 요구해 *선언됐으나 미주입*(fault×sigma 비호환 시 injector 조용한 no-op)을
    # 'incomplete' 로 검출. NONE 채널은 변형 출력 없음(no-op) → 추가 안 함.
    faulted_topic = FAULT_CHANNEL_FAULTED_TOPIC.get(fault_channel)
    if faulted_topic is not None:
        record_topics.append(faulted_topic)
    # B4(tier2): 게이트 accept-dispatch(SIGMA_FINAL=/intent/llm_sigma_raw)도 record.
    # (a) C3 "제안↔승인 계획 전수 로깅" 증거(/tier2/decision 결정과 쌍 — 승인되어
    # actuation 으로 흐른 σ). (b) bag_integrity 가 dispatch 수로 "게이트가 한 번이라도
    # accept 했는가"를 판정 → accept 0(전부 reject/confirm)이면 actuation·setpoint
    # 부재가 C3 정상 동작이라 setpoint 요구 면제, accept≥1 인데 setpoint 부재면 진짜
    # incomplete. hallucination 은 SIGMA_FINAL 이 이미 faulted 로 포함되므로 중복 방지.
    if config.tier2_enabled and SIGMA_FINAL not in record_topics:
        record_topics.append(SIGMA_FINAL)
    specs.append(NodeSpec(
        package='ros2',
        executable='bag record',
        name=NODE_NAME_ROSBAG,
        kind='process',
        parameters={
            'output': trial.trial_id,
            'topics': tuple(record_topics),
        },
    ))

    # 8. trial_utterance_pub — 항상. per-trial 사용자 발화를 raw 토픽에 N회 발행
    # (ADR-0030 F5). 종전 수동 `ros2 topic pub` 를 trial 합성으로 흡수 → nominal
    # 사슬(발화→wrapper σ→sigma_bridge→follower→tier1→setpoint_safe) 자동 구동.
    # rosbag 다음(마지막)이라 recorder 가 먼저 구독 establish 후 발화가 흐름. --rate
    # 로 발행이 수 초 퍼져 wrapper 구독 race 회피.
    specs.append(NodeSpec(
        package='ros2',
        executable='topic pub',
        name=NODE_NAME_UTTERANCE,
        kind='process',
        parameters={
            'topic': UTTERANCE_RAW_TOPIC,
            'message': scenario_utterance(trial.scenario_id),
            'times': _UTTERANCE_TIMES,
            'rate': _UTTERANCE_RATE_HZ,
        },
    ))

    return specs


def expected_node_count(trial: TrialSpec) -> int:
    """B0/B1/B2/B3/B4 측 expected node count = 6/6/6/7/8.

    test 측 *합성 결과* ↔ *expected count* 측 정합 검증 측 helper. (ADR-0030 F5
    per-trial 발화 publisher 추가로 종전 5/5/5/6/7 → +1.) confidence_source=
    'synthetic:*' 이면 publisher_node 1개 추가 (ADR-0050 D7 안 B).
    """
    config = trial.baseline_config
    count = 6  # tier1 + intent_llm + estimator + injector + rosbag2 + utterance_pub
    if config.context_aug:
        count += 1  # context_graph
    if config.tier2_enabled:
        count += 1  # tier2_gate
    if trial.confidence_source.startswith('synthetic:'):
        count += 1  # confidence publisher_node (external 모드 입력원)
    return count
