"""pytest conftest — intent_confidence 패키지 + scenario_params import path 추가.

host venv 측 pytest 실행 시 (a) 본 ament_python 패키지(intent_confidence), (b)
grounding 의 동의어 단일 소스인 scenario_params(sim/scenario_params — pure Python,
rclpy 불요)를 직접 import 가능하게 한다. node 모듈은 rclpy 의존이라 host import
불가 — 테스트는 pure 모듈(grounding·live_signals·estimator)만 import.
"""

from __future__ import annotations

import sys
from pathlib import Path


_PKG_DIR = Path(__file__).resolve().parent
_SCENARIO_PARAMS_DIR = _PKG_DIR.parent.parent / 'sim' / 'scenario_params'
for _p in (_PKG_DIR, _SCENARIO_PARAMS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
