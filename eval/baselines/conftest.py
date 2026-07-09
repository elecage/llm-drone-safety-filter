"""pytest conftest — eval_baselines package import PYTHONPATH 추가.

eval/baselines/ 측 ament_python 패키지가 host venv 측 pytest 실행 시 직접
import 가능하도록 path 추가.
"""

from __future__ import annotations

import sys
from pathlib import Path


_BASELINES_DIR = Path(__file__).resolve().parent
if str(_BASELINES_DIR) not in sys.path:
    sys.path.insert(0, str(_BASELINES_DIR))
