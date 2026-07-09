"""eval_metrics schemas — TimeSeries + TrialMetadata frozen dataclass.

metric 측 input data 측 표준 표현. bag_reader (B6b 후속) 측 rosbag2 측
trial bag 측 시계열 / event 추출 후 본 schemas 측 dataclass 측 instantiate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


def clamp_monotonic(timestamps: Sequence[float]) -> Tuple[float, ...]:
    """timestamp 열을 비감소로 단조화 — 역전 sample 을 직전 값으로 clamp.

    레코더/전송 jitter(인접 sample 의 sub-ms 역전, ADR-0039 D5) 흡수. 종전엔
    다운스트림(`TimeSeries`·`extract_loop_periods`·`build_drone_position`)이 단조성을
    raise 로 강제해 *단일 trial 의 sub-ms 역전이 전체 집계를 크래시*시켰다(2026-07-01).
    값 영향 = 해당 segment 간격 0 (sub-ms, metric 무시 가능). magnitude 무관하게
    순서만 정렬하므로 데이터 손실 없음.
    """
    out: list = []
    prev = None
    for t in timestamps:
        t = float(t)
        if prev is not None and t < prev:
            t = prev
        out.append(t)
        prev = t
    return tuple(out)


@dataclass(frozen=True)
class TimeSeries:
    """단조 비감소 timestamp 측 시계열 (timestamps + values, 동일 길이).

    Fields:
        timestamps: [s] (정합 단위 = 초). 단조 비감소 강제 — bag_reader 측
            rosbag2 message timestamp 측 추출.
        values: timestamps 와 동일 길이.

    safety.py 측 ``h(x(t))`` 시계열 / overconservativeness.py 측 ``r(\\tilde c(t))``
    시계열 / latency.py 측 ``\\tau_\\text{loop}(t)`` 시계열 측 input.
    """

    timestamps: Tuple[float, ...]
    values: Tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.values):
            raise ValueError(
                f'timestamps + values 동일 길이 필요 — '
                f'got len(timestamps)={len(self.timestamps)} != '
                f'len(values)={len(self.values)}'
            )
        if len(self.timestamps) == 0:
            return  # 빈 시계열 OK (metric 측 별도 처리)
        # 단조 비감소 — 레코더/전송 jitter 로 인접 sample 이 역전 기록될 수 있어
        # (ADR-0039 D5; 종전엔 raise 해 단일 trial 의 sub-ms 역전이 전체 집계를
        # 크래시시킴, 2026-07-01 적발) 직전 값으로 clamp 한다(값 영향 = sub-ms, 무시).
        clamped = clamp_monotonic(self.timestamps)
        if clamped != self.timestamps:
            object.__setattr__(self, 'timestamps', clamped)


@dataclass(frozen=True)
class TrialMetadata:
    """paper §C trial 측 메타데이터 (ADR-0025 D4 trial_meta.yaml 정합).

    Fields:
        scenario: 'S5' | 'S6' | 'S7' | 'S8' (ADR-0006 indoor 4 시나리오).
        baseline: 'B0' | 'B1' | 'B2' | 'B3' | 'B4' (ADR-0005 D6 + ADR-0018 D3).
        fault_class: 'none' | 'hallucination' | 'adversarial' | 'cognitive_lapse'
            | 'attribute_mismatch'.
        fault_variant: fault_class 측 variant string (none 측 None).
        seed: rng seed (재현성).
        wall_clock_s: trial 측 *실 측정* episode 길이 [s] — bag_reader 측 bag
            duration 측 추출.
        bag_status: 'complete' | 'incomplete' | 'unknown' |
            'fault_not_applicable' — bag 무결성 판정
            (`eval_runner.bag_integrity`, 세션 34 리뷰 P2 후속). 'unknown' =
            본 필드 도입 전 legacy trial_meta.yaml (키 부재 → loader default).
            'fault_not_applicable' = 제3 범주 (ADR-0037 amend) — 의도 계층의
            명료화 후퇴로 주입 미정의, 결함 통계 산입 금지(별도 보고).
            **집계 측 'incomplete'/'unknown'/'fault_not_applicable' trial 은
            명시 보고 의무 — 조용한 제외(silent drop) 금지.**
    """

    scenario: str
    baseline: str
    fault_class: str
    fault_variant: str
    seed: int
    wall_clock_s: float
    bag_status: str = 'unknown'

    _ALLOWED_SCENARIOS = ('S5', 'S6', 'S7', 'S8')
    _ALLOWED_BASELINES = ('B0', 'B1A', 'B1B', 'B2', 'B3', 'B4')
    _ALLOWED_FAULT_CLASSES = (
        'none', 'hallucination', 'adversarial',
        'cognitive_lapse', 'attribute_mismatch',
    )
    _ALLOWED_BAG_STATUSES = (
        'complete', 'incomplete', 'unknown', 'fault_not_applicable',
    )

    def __post_init__(self) -> None:
        if self.scenario not in self._ALLOWED_SCENARIOS:
            raise ValueError(
                f'scenario 측 {self._ALLOWED_SCENARIOS} — got {self.scenario!r}'
            )
        if self.baseline not in self._ALLOWED_BASELINES:
            raise ValueError(
                f'baseline 측 {self._ALLOWED_BASELINES} — got {self.baseline!r}'
            )
        if self.fault_class not in self._ALLOWED_FAULT_CLASSES:
            raise ValueError(
                f'fault_class 측 {self._ALLOWED_FAULT_CLASSES} — '
                f'got {self.fault_class!r}'
            )
        if self.fault_class == 'none':
            if self.fault_variant not in ('', 'none'):
                raise ValueError(
                    f'fault_class=none 측 fault_variant 측 \"\" 또는 \"none\" — '
                    f'got {self.fault_variant!r}'
                )
        else:
            if not self.fault_variant.strip():
                raise ValueError(
                    f'fault_class={self.fault_class} 측 fault_variant 필수 — '
                    f'got {self.fault_variant!r}'
                )
        if self.wall_clock_s <= 0.0:
            raise ValueError(
                f'wall_clock_s 양의 실수 — got {self.wall_clock_s}'
            )
        if self.bag_status not in self._ALLOWED_BAG_STATUSES:
            raise ValueError(
                f'bag_status 측 {self._ALLOWED_BAG_STATUSES} — '
                f'got {self.bag_status!r}'
            )
