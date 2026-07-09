"""Pure-Python signal scenario loader + evaluator (ROS 의존성 X).

estimator_node.py 의 *비-rclpy 부분* 을 분리한 모듈. host venv 에서도 test 가능.

YAML signal scenario 스키마는 estimator_node 모듈 docstring 참조.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Tuple

import yaml

from intent_confidence.estimator import GInputs


@dataclass
class SignalSegment:
    duration_s: float
    type: str  # 'constant' | 'ramp' | 'step'
    s1: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    s1_from: float = 0.0
    s1_to: float = 0.0
    s2_from: float = 0.0
    s2_to: float = 0.0
    s3_from: float = 0.0
    s3_to: float = 0.0
    s1_absent: bool = False
    s2_absent: bool = False
    s3_absent: bool = False
    note: str = ''


@dataclass
class SignalScenario:
    name: str
    description: str
    publish_rate_hz: float
    finish_hover_s: float
    dot_c_max_yaml: float  # -1 = 미지정
    segments: List[SignalSegment] = field(default_factory=list)


@dataclass
class EstimatorReport:
    """진단 채널 payload (ADR-0020 D3 amendment).

    paper §C 가 *(a) 부재 vs (b) 낮은 신뢰도* 분리 보고 의무 충족용.
    """
    stamp_ns: int
    elapsed_s: float
    scenario_name: str
    segment_idx: int
    s1: float
    s2: float
    s3: float
    s1_absent: bool
    s2_absent: bool
    s3_absent: bool
    c_raw: float
    c_tilde: float
    c_tilde_prev: float
    dot_c_max: float
    delta_c_clamped: bool
    delta_c_requested: float
    delta_c_applied: float
    # live 모드 진단 (ADR-0020 D3 — 부재 사유 분리). synthesis 모드는 default 유지.
    s1_reason: str = 'ok'      # 'ok'|'no_detections'|'no_referent'|'no_match'|'stale'
    referent_labels: str = ''  # live 모드 referent (target_id), 쉼표 구분
    n_detections: int = -1     # live 모드 OVD detection 수 (-1 = synthesis)
    # referent latch 진단 (ADR-0020 amendment 2026-06-11 — 발견 A).
    sigma_age_s: float = -1.0  # 마지막 sigma 수신 후 경과 [s] (-1 = 미수신/synthesis)
    sigma_latched: bool = False  # 활성 sigma latch 여부
    # s3 구조적 부재 진단 (ADR-0020 D8) — *구조적 제외*(백본 무능력, c=s1·s2) 인지
    # *런타임 0*(s3_absent) 인지 honest 보고. 백본별 유효 신호 집합 구분
    # (cloud {s1,s2,s3} / edge {s1,s2}). synthesis 모드는 default False.
    s3_structural: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# cmsm-proof §7.1 P1-P5 시안 default — launch 인자·YAML 둘 다 미지정 시 fallback.
DOT_C_MAX_DEFAULT = 0.833


def resolve_dot_c_max(arg: float, yaml_value: float) -> float:
    """dot_c_max 우선순위 chain (PR #69 review §1 follow-up).

    sentinel = 음수 (또는 0) → "미지정". 우선순위:

    1. ``arg > 0``       → launch 인자 명시 (최우선)
    2. ``yaml_value > 0``→ YAML 의 ``dot_c_max:`` 키 명시
    3. 둘 다 미지정     → ``DOT_C_MAX_DEFAULT`` (cmsm-proof §7.1 시안)

    Returns:
        해결된 dot_c_max 값 (양의 실수).
    """
    if arg > 0.0:
        return arg
    if yaml_value > 0.0:
        return yaml_value
    return DOT_C_MAX_DEFAULT


def _warn_absent_conflict(
    raw: dict, signal_name: str, value_keys: tuple, absent_key: str,
) -> None:
    """absent=True 인데 값이 0 이외로 명시되면 warning (PR #69 review §3 follow-up).

    값은 compute_g 에서 어차피 무시되지만 *의도된 시나리오인지 오타인지*
    디버깅 측 분리 위해 명시 알림.
    """
    if not raw.get(absent_key, False):
        return
    for vk in value_keys:
        if vk in raw and float(raw[vk]) != 0.0:
            warnings.warn(
                f'{signal_name}_absent=True 인데 {vk}={raw[vk]} 명시됨 — '
                f'compute_g 에서 입력값 무시 (의도된 거동이면 무시해도 OK). '
                f'segment note 에 의도 명시 권장.',
                stacklevel=3,
            )
            return  # 한 신호당 한 번만 warn


def load_signal_scenario(path: Path) -> SignalScenario:
    with path.open('r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    segments: List[SignalSegment] = []
    raw_segments = data.get('segments', [])
    # PR #69 review §4 follow-up — 빈 segments YAML 거부 (silent 진행 방지).
    if not raw_segments:
        raise ValueError(
            f'segments 비어 있음 — 최소 1 segment 필요 ({path}). '
            f'시나리오 의도 미명확 시 constant 1 segment 라도 명시.'
        )
    for raw in raw_segments:
        stype = str(raw.get('type', 'constant')).lower()
        if stype not in ('constant', 'ramp', 'step'):
            raise ValueError(f'segment.type 무효: "{stype}" (constant|ramp|step만)')

        common = dict(
            duration_s=float(raw['duration_s']),
            type=stype,
            note=str(raw.get('note', '')),
            s1_absent=bool(raw.get('s1_absent', False)),
            s2_absent=bool(raw.get('s2_absent', False)),
            s3_absent=bool(raw.get('s3_absent', False)),
        )
        # absent=True + 비-0 값 명시 → warning (PR #69 review §3).
        _warn_absent_conflict(raw, 's1', ('s1', 's1_from', 's1_to'), 's1_absent')
        _warn_absent_conflict(raw, 's2', ('s2', 's2_from', 's2_to'), 's2_absent')
        _warn_absent_conflict(raw, 's3', ('s3', 's3_from', 's3_to'), 's3_absent')
        if stype == 'ramp':
            seg = SignalSegment(
                **common,
                s1_from=_clamp01(float(raw.get('s1_from', raw.get('s1', 1.0)))),
                s1_to=_clamp01(float(raw.get('s1_to', raw.get('s1', 1.0)))),
                s2_from=_clamp01(float(raw.get('s2_from', raw.get('s2', 1.0)))),
                s2_to=_clamp01(float(raw.get('s2_to', raw.get('s2', 1.0)))),
                s3_from=_clamp01(float(raw.get('s3_from', raw.get('s3', 1.0)))),
                s3_to=_clamp01(float(raw.get('s3_to', raw.get('s3', 1.0)))),
            )
        else:  # constant / step
            seg = SignalSegment(
                **common,
                s1=_clamp01(float(raw.get('s1', 1.0))),
                s2=_clamp01(float(raw.get('s2', 1.0))),
                s3=_clamp01(float(raw.get('s3', 1.0))),
            )
        segments.append(seg)

    return SignalScenario(
        name=str(data.get('name', path.stem)),
        description=str(data.get('description', '')),
        publish_rate_hz=float(data.get('publish_rate_hz', 10.0)),
        finish_hover_s=float(data.get('finish_hover_s', 2.0)),
        dot_c_max_yaml=float(data.get('dot_c_max', -1.0)),
        segments=segments,
    )


def segment_starts_seconds(scenario: SignalScenario) -> List[float]:
    starts: List[float] = []
    acc = 0.0
    for s in scenario.segments:
        starts.append(acc)
        acc += s.duration_s
    return starts


def evaluate_signals_at(
    scenario: SignalScenario,
    segment_starts_s: List[float],
    elapsed_s: float,
) -> Tuple[GInputs, int]:
    """현 시점 raw 신호 + 현재 segment 인덱스."""
    if not scenario.segments:
        return GInputs(s1=1.0, s2=1.0, s3=1.0), -1

    total_end = segment_starts_s[-1] + scenario.segments[-1].duration_s
    if elapsed_s >= total_end:
        last = scenario.segments[-1]
        if last.type == 'ramp':
            s = GInputs(
                s1=last.s1_to, s2=last.s2_to, s3=last.s3_to,
                s1_absent=last.s1_absent, s2_absent=last.s2_absent, s3_absent=last.s3_absent,
            )
        else:
            s = GInputs(
                s1=last.s1, s2=last.s2, s3=last.s3,
                s1_absent=last.s1_absent, s2_absent=last.s2_absent, s3_absent=last.s3_absent,
            )
        return s, len(scenario.segments) - 1

    idx = 0
    for i, start in enumerate(segment_starts_s):
        if elapsed_s >= start:
            idx = i
        else:
            break

    seg = scenario.segments[idx]
    seg_t = elapsed_s - segment_starts_s[idx]

    if seg.type == 'ramp':
        frac = max(0.0, min(1.0, seg_t / max(seg.duration_s, 1e-9)))
        s = GInputs(
            s1=seg.s1_from + frac * (seg.s1_to - seg.s1_from),
            s2=seg.s2_from + frac * (seg.s2_to - seg.s2_from),
            s3=seg.s3_from + frac * (seg.s3_to - seg.s3_from),
            s1_absent=seg.s1_absent, s2_absent=seg.s2_absent, s3_absent=seg.s3_absent,
        )
    else:
        s = GInputs(
            s1=seg.s1, s2=seg.s2, s3=seg.s3,
            s1_absent=seg.s1_absent, s2_absent=seg.s2_absent, s3_absent=seg.s3_absent,
        )

    return s, idx
