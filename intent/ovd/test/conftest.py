"""pytest 설정 — `requires_weights` 마커 등록.

`@pytest.mark.requires_weights` 는 실제 YOLO-World weight 가 있어야만
의미있는 *통합* 테스트에 부착. 디폴트로는 SKIP — 환경 변수
``OVD_RUN_INTEGRATION=1`` 또는 ``-m requires_weights`` 명시 시에만 실행.

이렇게 분리한 이유:
- 기본 pytest 실행이 weight 다운로드 (~300 MB) 를 강제하지 않음.
- CI 가벼움 보존. paper §C 실험 트랙에서 별도 통합 셋 돌림.
- mock 기반 단위 테스트로 wrapper 로직 자체는 항상 검증.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_weights: 실제 YOLO-World weight 가 있어야만 도는 통합 테스트. "
        "OVD_RUN_INTEGRATION=1 또는 '-m requires_weights' 로만 실행.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if os.environ.get("OVD_RUN_INTEGRATION") == "1":
        return  # 통합 셋 명시 활성화 — skip 안 함.
    # '-m requires_weights' 명시 시도 도는 코드 경로는 marker 표현식 평가 결과로
    # pytest 가 알아서 처리하므로, 우리는 *디폴트* (마커 표현식 없음) 인 경우만
    # 스킵 처리.
    if config.getoption("markexpr"):
        return
    skip_integration = pytest.mark.skip(
        reason="실 weight 필요 — OVD_RUN_INTEGRATION=1 또는 -m requires_weights",
    )
    for item in items:
        if "requires_weights" in item.keywords:
            item.add_marker(skip_integration)
