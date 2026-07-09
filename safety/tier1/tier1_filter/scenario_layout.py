"""Scenario layout — tier1_filter 접근 편의 facade.

단일 진실 소스: scenario_params.params (sim/scenario_params/).
본 모듈은 tier1_filter 패키지 내부 import 편의 + 기존 공개 API 유지.

host venv pytest 실행 시 conftest.py 측 scenario_params 경로 자동 주입.
ROS 2 colcon build 환경에선 install/setup.bash 측 자동 처리.
"""

from __future__ import annotations

from typing import Any, Dict

from scenario_params.params import (  # noqa: F401  (facade re-export)
    VALID_SCENARIO_IDS,
    VALID_SCENARIOS,
    cbf_availability_margin,
    is_cbf_available,
    tier1_cbf_params,
    tier1_local_params,
)

# 기존 launch 파일 + 테스트 측 SCENARIO_USER_PARAMS 직접 참조 — facade 유지.
SCENARIO_USER_PARAMS: Dict[str, Dict[str, float]] = {
    s: tier1_local_params(s) for s in sorted(VALID_SCENARIOS)
}


def resolve_scenario_params(scenario: str) -> Dict[str, Any]:
    """scenario lookup + 검증 + dict 복사 반환 (caller mutation 회피).

    Args:
        scenario: 'livingroom' (default) | 'yard'.

    Returns:
        dict — user_local_x/y/z + r_min (4 keys).

    Raises:
        RuntimeError: scenario 측 unknown.
    """
    return tier1_local_params(scenario)
