"""pytest conftest — eval_runner package + sibling eval_baselines·eval_faults import 경로 추가.

eval/runner/ 측 ament_python 패키지가 host venv 측 pytest 실행 시 직접 import
가능하도록 path 추가. 본 runner core 가 eval_baselines (BaselineMode·BaselineConfig
+ b{N}_config helper) + eval_faults (FaultScenario·FaultChannel + load_fault_scenario)
+ eval_metrics (bag_pipeline·trial_meta read side·TrialMetadata) 를 의존하므로
sibling 패키지 경로도 함께 추가. (metrics 는 세션 34 P2 후속에서 추가 — 종전
`cd eval/runner` 단독 pytest 시 test_bag_pipeline/test_trial_meta 가
ModuleNotFoundError 로 수집 실패하던 잠재 결함 정정.)
"""

from __future__ import annotations

import sys
from pathlib import Path


_RUNNER_DIR = Path(__file__).resolve().parent
_EVAL_DIR = _RUNNER_DIR.parent
_ROOT = _EVAL_DIR.parent

for sibling in ('runner', 'baselines', 'faults', 'metrics'):
    pkg_path = _EVAL_DIR / sibling
    pkg_str = str(pkg_path)
    if pkg_path.is_dir() and pkg_str not in sys.path:
        sys.path.insert(0, pkg_str)

# panel.py 측 scenario_params (SCENARIO_LOCATION 단일 소스) + intent_llm (backbone
# registry) 의존. metrics_aggregator/task_success_geom 측 intent_sigma_bridge
# (vantage 기하 helper, ADR-0032 미해결 2 — 단일 진실 소스 재사용) 의존.
for _ext in ('sim/scenario_params', 'intent/llm', 'intent/sigma_bridge'):
    _ext_path = _ROOT / _ext
    if _ext_path.is_dir() and str(_ext_path) not in sys.path:
        sys.path.insert(0, str(_ext_path))
