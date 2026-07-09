"""pytest conftest — intent_loop package import PYTHONPATH 추가."""

from __future__ import annotations

import sys
from pathlib import Path

_LOOP_DIR = Path(__file__).resolve().parent
if str(_LOOP_DIR) not in sys.path:
    sys.path.insert(0, str(_LOOP_DIR))
