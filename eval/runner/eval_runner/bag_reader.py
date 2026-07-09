"""rosbag2 trial bag → ``BagInputs`` 추출 (B6c — bag_pipeline 책임표의 #6c wrapper).

[ADR-0025 D4](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d4)
record 6 토픽을 rosbag2_py 로 읽어 `bag_pipeline.BagInputs` 로 변환한다. 변환 결과는
`bag_pipeline.compute_trial_metrics` 가 메트릭 6종으로 소비한다.

## 설계 — I/O 와 순수 변환 분리

| 함수 | 책임 | 의존성 |
|---|---|---|
| `read_bag` | rosbag2_py SequentialReader I/O + 메시지 deserialize | ROS 2 (rosbag2_py·rclpy·rosidl) |
| `build_bag_inputs` | primitive lists → `BagInputs` (NED→ENU·episode 길이 산출) | host venv (pure) |

순수 변환은 `build_bag_inputs` 에 격리해 rosbag2_py 없이 단위 테스트한다. rosbag2_py 가
없는 host venv 에서 `read_bag` 은 명확한 `RuntimeError` 로 실패한다 (Docker colcon test
또는 Mac mini/맥북 ROS 2 환경에서 실 bag e2e).

## 토픽 ↔ 필드 (ADR-0025 D4 · bag_pipeline.BagInputs)

| 토픽 | 타입 | 추출 | BagInputs 필드 |
|---|---|---|---|
| `/fmu/out/vehicle_local_position_v1` | px4_msgs/VehicleLocalPosition (NED) | (x,y,z) → **ENU 변환** | drone_position_msgs |
| `/cmd/trajectory_setpoint_safe` | geometry_msgs/TwistStamped | **header.stamp** (sim time) | setpoint_timestamps_s |
| `/intent/estimator/report` | std_msgs/String (JSON) | msg.data | estimator_report_json_strs |
| `/tier2/decision` | std_msgs/String (JSON) | msg.data | tier2_decision_json_strs |
| `/clock` | rosgraph_msgs/Clock | episode 길이 산출 | (episode_duration_s) |
| `/intent/grounding_confidence` | std_msgs/Float32 | (진단 — 미사용) | — |

drone/report/clock 시계열은 *bag 기록 시각*(rosbag2 message timestamp)을 쓴다 — 서로 같은
기준이라 $h(x(t))$·$r(t)$ 동기(nearest-neighbor)에 정합.

> **setpoint τ_loop 는 header.stamp(sim time)로 측정 (세션 50 진단)**: rosbag2 message
> timestamp 는 *기록 시각*(wall-clock ROS_TIME)이라 recorder 워밍업·전송 jitter 가 섞인다
> (정상 trial 의 시작부에서 0.29 s spike 관측 — 같은 순간 header gap 은 0.050 s 정상,
> `/clock` 도 끊김). τ_loop 는 결정론 루프 *주기*(RQ3, $\\leq 50$ ms)를 재는 metric 이므로
> setpoint 가 실제 발행된 sim-time(`header.stamp`, filter_node 가 nominal header 를 그대로
> 복사 → nominal source 의 발행 cadence)으로 inter-message dt 를 잡는다. drone/report/clock
> 은 종전대로 bag 기록 시각(서로 동일 기준이라 동기 정합).

## NED → ENU

filter_node 와 동일 규칙: $x_\\text{enu} = y_\\text{ned},\\ y_\\text{enu} = x_\\text{ned},\\
z_\\text{enu} = -z_\\text{ned}$. user_position(`scenario_params`) 이 local ENU 이므로 거리
계산(`positions_to_h_series`) 정합을 위해 드론 위치도 ENU 로 맞춘다.

> **위치 토픽 (P4-1 정정)**: record 토픽은 PX4 uXRCE-DDS 실 발행
> `/fmu/out/vehicle_local_position_v1` 을 직접 기록한다(ADR-0025 D4 amendment, P4-1). 종전
> 계약명 `/vehicle_local_position` 은 *아무도 발행하지 않아* bag 에 위치가 안 들어가던 불일치를
> 해소(세션 43 발견). NED 좌표라 본 모듈이 ENU 로 변환한다. `TOPIC_POSITION` 으로 조정 가능.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from eval_runner.bag_pipeline import BagInputs

# ROS 2 런타임 (rosbag2_py·rclpy·rosidl) 은 colcon 빌드 환경에서만 가용 —
# host venv 에서는 순수 변환(build_bag_inputs)만 테스트하도록 조건부 import.
try:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message

    HAS_ROSBAG2 = True
except ImportError:  # pragma: no cover - 환경 의존
    HAS_ROSBAG2 = False


# ADR-0025 D4 record 토픽 계약명 (launch_composition rosbag2 NodeSpec 정합).
TOPIC_POSITION = '/fmu/out/vehicle_local_position_v1'
TOPIC_SETPOINT = '/cmd/trajectory_setpoint_safe'
TOPIC_ESTIMATOR_REPORT = '/intent/estimator/report'
TOPIC_TIER2_DECISION = '/tier2/decision'
TOPIC_CLOCK = '/clock'


# -------------------------------------------------------------------- 순수 변환


def ned_to_enu(xyz_ned: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """NED (x, y, z) → ENU (y, x, -z). filter_node `_on_vehicle_local_position` 정합."""
    x, y, z = xyz_ned
    return (y, x, -z)


def stamp_to_s(stamp) -> float:
    """ROS ``builtin_interfaces/Time`` (header.stamp) → 초 [s].

    setpoint τ_loop 를 *bag 기록 시각*(wall-clock)이 아니라 메시지가 실제 발행된
    sim-time 으로 재기 위한 변환 (모듈 docstring "setpoint τ_loop" 노트). 순수 함수라
    rosbag2 없이 host 단위 테스트한다 (``.sec``/``.nanosec`` 만 요구하는 duck typing).
    """
    return int(stamp.sec) + int(stamp.nanosec) * 1e-9


def _resolve_episode_duration(
    override_s: Optional[float],
    clock_ts: List[float],
    drone_ts: List[float],
    setpoint_ts: List[float],
    report_ts: List[float],
) -> float:
    """episode 길이 [s] 산출.

    우선순위: (1) override(trial_meta wall_clock 등 caller 제공) → (2) `/clock` span →
    (3) 전 토픽 timestamp span. 모두 불가(샘플 < 2)면 ValueError.
    """
    if override_s is not None:
        return float(override_s)
    if len(clock_ts) >= 2:
        return clock_ts[-1] - clock_ts[0]
    all_ts = [*clock_ts, *drone_ts, *setpoint_ts, *report_ts]
    if len(all_ts) >= 2:
        return max(all_ts) - min(all_ts)
    raise ValueError(
        'episode_duration_s 산출 불가 — `/clock` 또는 토픽 timestamp 가 최소 2 sample '
        '필요. bag 이 비었거나 단일 message 만 기록됨. episode_duration_s 인자로 '
        'trial_meta wall_clock 을 명시 전달하라.'
    )


def build_bag_inputs(
    drone_position_ned: List[Tuple[float, Tuple[float, float, float]]],
    setpoint_timestamps_s: List[float],
    estimator_report_json_strs: List[Tuple[float, str]],
    tier2_decision_json_strs: List[str],
    clock_timestamps_s: List[float],
    *,
    episode_duration_s: Optional[float] = None,
) -> BagInputs:
    """primitive lists → ``BagInputs`` (NED→ENU 변환 + episode 길이 산출, 순수).

    Args:
        drone_position_ned: ``[(t_s, (x, y, z)_NED), ...]`` — `/vehicle_local_position`.
        setpoint_timestamps_s: `/cmd/trajectory_setpoint_safe` header.stamp(sim time) [s].
        estimator_report_json_strs: ``[(t_s, json_str), ...]`` — `/intent/estimator/report`.
        tier2_decision_json_strs: `/tier2/decision` JSON strings (timestamp 불요).
        clock_timestamps_s: `/clock` bag timestamps (episode 길이 산출용).
        episode_duration_s: 명시 길이(trial_meta wall_clock). None 이면 clock/전체 span.

    Returns:
        BagInputs — 검증(episode_duration > 0, setpoint >= 2 sample)은
        `BagInputs.__post_init__` 가 수행.
    """
    drone_position_enu = [
        (t_s, ned_to_enu(xyz)) for t_s, xyz in drone_position_ned
    ]
    duration = _resolve_episode_duration(
        episode_duration_s,
        clock_timestamps_s,
        [t for t, _ in drone_position_ned],
        list(setpoint_timestamps_s),
        [t for t, _ in estimator_report_json_strs],
    )
    return BagInputs(
        drone_position_msgs=drone_position_enu,
        setpoint_timestamps_s=list(setpoint_timestamps_s),
        estimator_report_json_strs=list(estimator_report_json_strs),
        tier2_decision_json_strs=list(tier2_decision_json_strs),
        episode_duration_s=duration,
    )


# -------------------------------------------------------------------- rosbag2 I/O


def _require_rosbag2() -> None:
    if not HAS_ROSBAG2:
        raise RuntimeError(
            'rosbag2_py 미설치 — read_bag 은 ROS 2(colcon) 환경 전용. host venv 에서는 '
            'build_bag_inputs(순수)만 사용 가능. Docker colcon test 또는 Mac mini/맥북 '
            'sourced 환경에서 실행하라.'
        )


def read_bag(
    bag_dir: Path | str,
    *,
    episode_duration_s: Optional[float] = None,
    storage_id: str = 'sqlite3',
) -> BagInputs:
    """rosbag2 디렉토리 → ``BagInputs``.

    record 6 토픽을 순회하며 값을 deserialize 하고 `build_bag_inputs` 로 변환한다.
    drone/report/clock 시계열은 bag 기록 시각(message timestamp)을, setpoint(τ_loop)는
    `header.stamp`(sim time)을 쓴다 (모듈 docstring "setpoint τ_loop" 노트).

    Args:
        bag_dir: rosbag2 디렉토리 (metadata.yaml + db).
        episode_duration_s: 명시 episode 길이(trial_meta wall_clock). None 이면 bag span.
        storage_id: rosbag2 storage plugin (기본 'sqlite3').

    Returns:
        BagInputs.

    Raises:
        RuntimeError: rosbag2_py 미설치 또는 bag 디렉토리 부재.
        ValueError: episode 길이 산출 불가 또는 BagInputs 검증 실패.
    """
    _require_rosbag2()
    bag_path = Path(bag_dir)
    if not bag_path.exists():
        raise RuntimeError(f'bag 디렉토리 부재: {bag_path}')

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(bag_path), storage_id=storage_id),
        rosbag2_py.ConverterOptions(
            input_serialization_format='cdr',
            output_serialization_format='cdr',
        ),
    )
    type_map = {t.name: t.type for t in reader.get_all_topics_and_types()}

    drone_position_ned: List[Tuple[float, Tuple[float, float, float]]] = []
    setpoint_timestamps_s: List[float] = []
    estimator_report_json_strs: List[Tuple[float, str]] = []
    tier2_decision_json_strs: List[str] = []
    clock_timestamps_s: List[float] = []

    def _msg_cls(topic: str):
        return get_message(type_map[topic])

    while reader.has_next():
        topic, data, t_ns = reader.read_next()
        t_s = t_ns * 1e-9
        if topic == TOPIC_POSITION:
            msg = deserialize_message(data, _msg_cls(topic))
            drone_position_ned.append(
                (t_s, (float(msg.x), float(msg.y), float(msg.z)))
            )
        elif topic == TOPIC_SETPOINT:
            # τ_loop 는 sim-time 루프 주기 — bag 기록 시각(wall-clock, recorder jitter
            # 포함) 이 아니라 header.stamp(filter_node 가 nominal header 복사 → 발행 sim
            # time)로 inter-message dt 를 잡는다 (모듈 docstring "setpoint τ_loop" 노트).
            msg = deserialize_message(data, _msg_cls(topic))
            setpoint_timestamps_s.append(stamp_to_s(msg.header.stamp))
        elif topic == TOPIC_ESTIMATOR_REPORT:
            msg = deserialize_message(data, _msg_cls(topic))
            estimator_report_json_strs.append((t_s, str(msg.data)))
        elif topic == TOPIC_TIER2_DECISION:
            msg = deserialize_message(data, _msg_cls(topic))
            tier2_decision_json_strs.append(str(msg.data))
        elif topic == TOPIC_CLOCK:
            clock_timestamps_s.append(t_s)

    return build_bag_inputs(
        drone_position_ned,
        setpoint_timestamps_s,
        estimator_report_json_strs,
        tier2_decision_json_strs,
        clock_timestamps_s,
        episode_duration_s=episode_duration_s,
    )
