"""pytest conftest — intent_sigma_bridge package import PYTHONPATH 추가.

intent/sigma_bridge/ 측 ament_python 패키지가 host venv 측 pytest 실행 시 직접
import 가능하도록 path 추가 (helper pure 수학 단위 테스트용 — node 모듈은
rclpy/px4_msgs 의존이라 host import 불가, test 는 helper 만 import).
helper 의 동의어 단일 소스(scenario_params.scene — pure Python)도 path 에 추가.
"""

from __future__ import annotations

import sys
from pathlib import Path


_PKG_DIR = Path(__file__).resolve().parent
_SCENARIO_PARAMS_DIR = _PKG_DIR.parent.parent / 'sim' / 'scenario_params'
for _p in (_PKG_DIR, _SCENARIO_PARAMS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
