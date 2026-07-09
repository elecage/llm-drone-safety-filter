"""pytest conftest — intent_stt package import PYTHONPATH 추가."""

from __future__ import annotations

import sys
from pathlib import Path

_STT_DIR = Path(__file__).resolve().parent
if str(_STT_DIR) not in sys.path:
    sys.path.insert(0, str(_STT_DIR))
