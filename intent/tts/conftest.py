"""pytest conftest — intent_tts package import PYTHONPATH 추가."""

from __future__ import annotations

import sys
from pathlib import Path

_TTS_DIR = Path(__file__).resolve().parent
if str(_TTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TTS_DIR))
