"""eval_calibration — paper §C 진입 0번 calibration package.

ADR-0025 D1.b (+ amendment 4·5·6·7·8) 의 calibration 절차 구현.
LLM 의 자연 환각 분포 σ_LLM,nat 사전 측정 → paper §C fault_variant Gaussian σ
mapping (1× / 5×) 의 입력값 생성.
"""

from eval_calibration.schemas import (
    Backbone,
    CalibrationResult,
    SampleOutput,
    ScenarioSpec,
    SigmaLlmNat,
    TypedAction,
)

__all__ = [
    'Backbone',
    'CalibrationResult',
    'SampleOutput',
    'ScenarioSpec',
    'SigmaLlmNat',
    'TypedAction',
]
