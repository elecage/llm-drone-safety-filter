import sys
from pathlib import Path

# 패키지 루트(sim/waypoint_follower)를 sys.path 에 추가 — host venv 단위 테스트가
# `waypoint_follower.waypoint_velocity` 를 colcon 빌드 없이 import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
