"""pytest conftest — intent_context + scenario_params 경로 주입 (host venv).

intent_context.context_graph 가 scenario_params (sim/scenario_params/) 를 의존.
ROS 2 colcon build 환경에선 install/setup.bash 측 자동 노출. host venv 측 보완.
"""

import sys
from pathlib import Path

_TEST_DIR = Path(__file__).resolve().parent
_PKG_DIR = _TEST_DIR.parent  # intent/context
_ROOT = _PKG_DIR.parents[1]  # LLM_Drone

for path in (_PKG_DIR, _ROOT / 'sim' / 'scenario_params'):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)
