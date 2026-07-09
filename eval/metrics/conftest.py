"""pytest conftest — eval_metrics 단위 테스트 측 PYTHONPATH 보강.

eval_metrics 측 pure-Python library (ament_python 아님) — PYTHONPATH 측
eval/metrics/ 측 직접 lookup.
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
