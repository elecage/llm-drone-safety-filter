"""ROS 2 launch description 합성 — NodeSpec → launch action.

[B7 #12 분할 2c](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
— `launch_composition.compose_trial_node_specs(trial)` 결과 (list[NodeSpec])
측 ROS 2 launch action 으로 변환. 분할 2a (`launch_composition.py`) 측 *pure
spec* 측 잠금 → 본 모듈 측 *실 launch action* 변환 + LaunchDescription 빌드.

## 책임 분리 (분할 2a vs 2c)

| 모듈 | 입력 | 출력 | 의존성 |
|---|---|---|---|
| `launch_composition.py` (2a) | `TrialSpec` | `list[NodeSpec]` (pure dataclass) | host venv (ROS 2 무관) |
| `launch_description.py` (2c) | `list[NodeSpec]` | `launch.LaunchDescription` | ROS 2 (`launch`, `launch_ros`) |

이 분리로 격자 enumeration / chain invariant / parameter wiring 검증은 host
venv 측 pytest 측 완전 cover, 실 launch object 빌드만 colcon test (Mac mini
Docker) 측 검증.

## host venv 측 단위 test 가능 영역

본 모듈 측 pure helper 측 host venv 측 검증 가능:
  - `node_spec_to_node_kwargs(spec)` — NodeSpec → `launch_ros.actions.Node(**kwargs)`
    측 kwargs dict 변환. `parameters` 측 ROS 2 convention 측 list-of-dict 형태
    wrap + `use_sim_time=True` 잠금 추가.
  - `process_spec_to_cmd(spec)` — rosbag2 process NodeSpec → `ros2 bag record`
    CLI 인자 list 변환.

ROS 2 의존 영역 (`build_launch_description` / `build_trial_launch_actions`) 측
test 측 `_ros2_launch_available()` helper (test 파일 측 정의) 측 `LaunchDescription`
+ `ExecuteProcess` + `Node` 실 import 측 확인 측 `pytest.mark.skipif` 측 gate —
host venv 측 skip, Mac mini Docker 측 colcon test 측 실 검증. `pytest.importorskip('launch')`
단독 측 사용 안 함 — PYTHONPATH 측 `eval/baselines/launch/` 디렉토리 측 namespace
package 'launch' 측 mask 측 false positive 회피.

## 토픽 사용 (잠금)

NodeSpec.parameters 측 토픽 이름 측 launch_composition.py 측 잠금 — 본 모듈
측 *변경 없이 그대로* parameters 측 전달. ADR-0025 D4 6 토픽 셋 측 rosbag2
process 측 CLI 인자 측 expansion (parameters['topics'] tuple → 개별 인자).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from eval_runner.launch_composition import (
    DEFAULT_BACKBONE,
    NODE_NAME_ROSBAG,
    NodeSpec,
    compose_trial_node_specs,
)
from eval_runner.schemas import TrialSpec


# 토픽 leading-slash 검증용 — rosbag2 record CLI 측 topic 인자 측 '/...' 형식 의무.
_TOPIC_SLASH_PREFIX = '/'


def node_spec_to_node_kwargs(spec: NodeSpec) -> Dict[str, Any]:
    """NodeSpec (kind='node') → `launch_ros.actions.Node(**kwargs)` 측 kwargs dict.

    paper §C trial 측 ROS 2 launch convention 잠금:
      - `output='screen'` — trial 측 stdout 측 monitor (기존 baseline launch 측 정합).
      - `parameters=[{...}]` — ROS 2 convention 측 list-of-dict (단일 dict wrap).
      - `'use_sim_time': True` — paper §C 측 sim 전용 trial (시계 source = gz_clock).

    Args:
        spec: NodeSpec (kind='node').

    Returns:
        kwargs dict — `launch_ros.actions.Node(**result)` 측 직접 사용 가능.

    Raises:
        ValueError: (a) spec.kind != 'node', (b) spec.parameters 측 `use_sim_time`
            명시 — paper §C 측 sim 전용 trial 측 `use_sim_time=True` 잠금 의도 측
            정합 (override 차단). 향후 hardware trial 진입 시 본 잠금 측 amendment
            예정.
    """
    if spec.kind != 'node':
        raise ValueError(
            f"node_spec_to_node_kwargs 측 kind='node' 만 — got {spec.kind!r}. "
            f"process kind 측 process_spec_to_cmd 사용."
        )
    if 'use_sim_time' in spec.parameters:
        raise ValueError(
            "spec.parameters 측 'use_sim_time' 명시 측 차단 — paper §C 측 sim 전용 "
            "trial 측 use_sim_time=True 잠금. 본 잠금 측 launch_description.py 측 "
            "단독 책임. hardware trial 진입 시 본 함수 측 amendment 필요."
        )
    merged_parameters: Dict[str, Any] = {'use_sim_time': True, **spec.parameters}
    return {
        'package': spec.package,
        'executable': spec.executable,
        'name': spec.name,
        'output': 'screen',
        'parameters': [merged_parameters],
    }


def process_spec_to_cmd(spec: NodeSpec) -> List[str]:
    """NodeSpec (kind='process') → ros2 CLI 인자 list — executable 별 dispatch.

    지원 process executable:
      - `'bag record'` (rosbag2): `parameters={'output', 'topics'}` →
        `ros2 bag record -o <output> <topic...>`.
      - `'topic pub'` (발화 publisher, ADR-0030 F5):
        `parameters={'topic', 'message', 'times', 'rate'}` →
        `ros2 topic pub --times N --rate R <topic> std_msgs/msg/String '{data: "<message>"}'`.

    Args:
        spec: NodeSpec (kind='process').

    Returns:
        CLI 인자 list — `launch.actions.ExecuteProcess(cmd=result)` 측 직접 사용.

    Raises:
        ValueError: spec.kind != 'process' 또는 executable 측 unsupported.
        KeyError: parameters 필수 키 누락.
    """
    if spec.kind != 'process':
        raise ValueError(
            f"process_spec_to_cmd 측 kind='process' 만 — got {spec.kind!r}. "
            f"node kind 측 node_spec_to_node_kwargs 사용."
        )
    if spec.executable == 'bag record':
        output = spec.parameters['output']
        topics: Sequence[str] = spec.parameters['topics']
        if not topics:
            raise ValueError("topics 빈 sequence 불가 — rosbag2 record 측 최소 1 토픽 필요.")
        for topic in topics:
            if not topic.startswith(_TOPIC_SLASH_PREFIX):
                raise ValueError(
                    f"topic={topic!r} 측 leading '/' 누락 — ROS 2 topic 측 절대 경로 의무."
                )
        return ['ros2', 'bag', 'record', '-o', str(output), *topics]
    if spec.executable == 'topic pub':
        topic = spec.parameters['topic']
        if not topic.startswith(_TOPIC_SLASH_PREFIX):
            raise ValueError(
                f"topic={topic!r} 측 leading '/' 누락 — ROS 2 topic 측 절대 경로 의무."
            )
        message = str(spec.parameters['message'])
        times = int(spec.parameters['times'])
        rate = float(spec.parameters['rate'])
        # ExecuteProcess(cmd=list)는 shell 무경유 — YAML arg 는 단일 리스트 원소.
        # 한국어/공백 발화는 따옴표 셸 이슈 없음(리스트 원소 리터럴). 메시지의 `"`는
        # 발화에 없으므로 미escape(필요 시 single-quote YAML 로).
        return [
            'ros2', 'topic', 'pub', '--times', str(times), '--rate', str(rate),
            topic, 'std_msgs/msg/String', f'{{data: "{message}"}}',
        ]
    # 후속 process kind 추가 시 dispatch 확장.
    raise ValueError(
        f"executable={spec.executable!r} 미지원 — 'bag record'|'topic pub' 만. "
        f"새 process executable 추가 시 본 함수 측 dispatch 확장 의무."
    )


def build_trial_launch_actions(
    trial: TrialSpec,
    backbone: str = DEFAULT_BACKBONE,
    bag_output: Optional[str] = None,
) -> List[Any]:
    """TrialSpec → list[launch action] 변환 (Node + ExecuteProcess).

    `compose_trial_node_specs(trial)` 측 NodeSpec list 측 launch action 측 1:1
    변환. 본 함수 측 ROS 2 (`launch`, `launch_ros`) 의존 — host venv 측 ImportError
    측 raise.

    Args:
        trial: TrialSpec.
        backbone: intent_llm registry 식별자.
        bag_output: 지정 시 rosbag2 `-o` 출력 경로를 *이 절대 경로*로 치환. compose
            는 `output=trial_id`(상대 bag 이름)만 알고 bag_dir(output_root/backbone/
            trial_id)은 runner 결정이라, 미지정 시 rosbag 이 CWD/trial_id 에 기록되어
            `check_bag_integrity(bag_dir)` 와 불일치(ADR-0030 D6 실측 발견). runner
            (`run_trial`)가 bag_dir 을 전달해 일치시킨다.

    Returns:
        list of launch_ros.actions.Node + launch.actions.ExecuteProcess (rosbag2 측만).
        ros2 launch convention 측 합성 순서 (`compose_trial_node_specs` docstring 측
        잠금) 유지.

    Raises:
        ImportError: host venv 측 ROS 2 unavailable.
        ValueError: NodeSpec kind 측 unsupported (분할 2a 측 validation 측 차단되므로
            정상 trial 측 발생 안 함).
    """
    # ROS 2 측 lazy import — host venv 측 본 함수 직접 호출 시만 ImportError.
    # pure helper (node_spec_to_node_kwargs · process_spec_to_cmd) 측 host venv
    # 측 그대로 동작.
    from launch.actions import ExecuteProcess  # noqa: WPS433 (local import 의도)
    from launch_ros.actions import Node  # noqa: WPS433

    specs = compose_trial_node_specs(trial, backbone)
    actions: List[Any] = []
    for spec in specs:
        if spec.kind == 'node':
            actions.append(Node(**node_spec_to_node_kwargs(spec)))
        elif spec.kind == 'process':
            cmd = process_spec_to_cmd(spec)
            if bag_output is not None and spec.name == NODE_NAME_ROSBAG:
                # `-o <output>` 의 output 을 절대 bag_dir 로 치환 (runner 결정).
                cmd[cmd.index('-o') + 1] = str(bag_output)
            actions.append(ExecuteProcess(
                cmd=cmd,
                # rosbag2 측 trial 종료 측 SIGINT 측 정상 종료 — launch 측 default
                # signal handling 사용. output='screen' 측 bag 측 진행 로그 capture.
                output='screen',
                name=spec.name,
            ))
        else:
            # launch_composition 측 VALID_NODE_KINDS 측 ('node', 'process') 잠금 —
            # 본 분기 측 unreachable. defense-in-depth 측 잠금.
            raise ValueError(f"NodeSpec.kind={spec.kind!r} 미지원.")
    return actions


def build_launch_description(
    trial: TrialSpec,
    backbone: str = DEFAULT_BACKBONE,
) -> Any:
    """TrialSpec → `launch.LaunchDescription` 측 빌드.

    paper §C trial 측 *최상위 launch entry point*. runner.py (B7 #12 분할 2d
    예정) 측 본 함수 호출 → ROS 2 launch service 측 시작 → trial 1 회 실행 + rosbag2
    record.

    Args:
        trial: TrialSpec.

    Returns:
        `launch.LaunchDescription` — total NodeSpec count 5/5/5/6/7 (B0/B1/B2/B3/B4)
        측 합성. 구성 = `launch_ros.actions.Node` (각 4/4/4/5/6 개) +
        `launch.actions.ExecuteProcess` (rosbag2 1 개, 항상).

    Raises:
        ImportError: host venv 측 ROS 2 unavailable (Mac mini Docker 측 colcon
            test 측 실 검증).
    """
    from launch import LaunchDescription  # noqa: WPS433

    actions = build_trial_launch_actions(trial, backbone)
    return LaunchDescription(actions)


def rosbag_node_spec(trial: TrialSpec) -> NodeSpec:
    """단일 trial 측 rosbag2 NodeSpec 측 helper — process spec 직접 접근 필요시.

    runner.py 측 trial_meta.yaml 측 rosbag2 output 측 경로 잠금 (ADR-0025 D4) 시
    본 helper 측 직접 NodeSpec 측 lookup. 구현 측 `NODE_NAME_ROSBAG` 측 *name
    lookup* (last element 측 직접 접근 안 함 — compose_trial_node_specs 측 합성
    순서 변경 측 견고화). 합성 순서 측 rosbag2 가 마지막 측 잠금 (분할 2a `compose_trial_node_specs`
    docstring) 이지만 본 helper 측 그 잠금 측 *의존하지 않음*.

    Args:
        trial: TrialSpec.

    Returns:
        rosbag2 NodeSpec (kind='process', name=NODE_NAME_ROSBAG).

    Raises:
        AssertionError: compose_trial_node_specs 측 rosbag2 spec 누락 (분할 2a
            invariant 위반 측만 발생, 정상 trial 측 unreachable).
    """
    specs = compose_trial_node_specs(trial)
    for spec in specs:
        if spec.name == NODE_NAME_ROSBAG:
            return spec
    raise AssertionError(
        "compose_trial_node_specs 결과 측 rosbag2 NodeSpec 누락 — launch_composition.py 측 invariant 위반."
    )
