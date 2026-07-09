"""pytest 경로 설정 — scenario_params 패키지 host venv 경로 주입.

ROS 2 colcon build 환경에선 install/setup.bash 측 scenario_params 자동 노출.
host venv (PYTHONPATH=.) 측 sim/scenario_params/ 미포함 → conftest 측 보완.
"""

import sys
from pathlib import Path

# /LLM_Drone/sim/scenario_params 를 sys.path 앞에 삽입
# parents[3]: safety/tier1/test → safety/tier1 → safety → LLM_Drone (root)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'sim' / 'scenario_params'))
