"""pytest conftest — eval_calibration 모듈 PYTHONPATH 추가.

eval_faults 가 TypedAction 등 eval_calibration 의 schema 를 *single source*
로 재사용 (코드 중복 회피). eval_calibration 측이 ament_python 미적용
pure-Python 이라 PYTHONPATH 측 lookup 필요.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CAL_PKG_DIR = _REPO_ROOT / 'eval' / 'calibration'

# eval/calibration/ 측 eval_calibration package import 가능하도록 path 추가
if _CAL_PKG_DIR.exists() and str(_CAL_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_CAL_PKG_DIR))

# eval/faults/ 자체도 (eval_faults 모듈 import 위해)
_FAULTS_DIR = Path(__file__).resolve().parent
if str(_FAULTS_DIR) not in sys.path:
    sys.path.insert(0, str(_FAULTS_DIR))
