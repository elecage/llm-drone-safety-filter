"""A4-3 — sim/worlds 의 SDF 에서 model world pose 자동 추출.

launch 진입 시 호출되어 ``known_objects`` / ``target_poses_json`` 파라미터를
자동 주입. 시나리오 SDF (S5/S6/S7 거실 등) 변경 시 launch 만 다시 띄우면
가구 좌표가 자동 동기.

규칙:
- SDF 의 ``<world><model name="X"><pose>x y z roll pitch yaw</pose>...</model>``
  를 찾아 (x, y, z) 만 추출 — yaw 등 회전 무시.
- model 의 *직접 자식* ``<pose>`` 만 사용 (link 내부 pose 무시).
- pose 문자열 파싱 실패 또는 짧으면 해당 model skip.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Dict, Tuple


Pose3 = Tuple[float, float, float]


def extract_model_poses(sdf_path: str) -> Dict[str, Pose3]:
    """SDF 파일에서 model name → (x, y, z) ENU world pose dict.

    파일 없거나 파싱 실패 시 빈 dict 반환 (launch fail-fast 안 함 — 호출자가 결정).
    """
    if not os.path.exists(sdf_path):
        return {}
    try:
        tree = ET.parse(sdf_path)
    except ET.ParseError:
        return {}

    out: Dict[str, Pose3] = {}
    for model in tree.getroot().iter('model'):
        name = model.get('name')
        if not name:
            continue
        pose_elem = model.find('pose')
        if pose_elem is None or pose_elem.text is None:
            continue
        parts = pose_elem.text.split()
        if len(parts) < 3:
            continue
        try:
            out[name] = (float(parts[0]), float(parts[1]), float(parts[2]))
        except ValueError:
            continue
    return out


def extract_model_poses_json(sdf_path: str) -> str:
    """target_poses_json 파라미터에 바로 넣을 수 있는 JSON 문자열."""
    poses = extract_model_poses(sdf_path)
    return json.dumps({k: list(v) for k, v in poses.items()})


def extract_known_objects_json(sdf_path: str, *, exclude_prefixes: tuple = ()) -> str:
    """known_objects_json 파라미터에 바로 넣을 수 있는 JSON 문자열.

    ``exclude_prefixes`` 에 해당하는 접두사로 시작하는 model 이름은 제외
    (예: ``wall_``, ``ceiling``, ``ground`` 등 — known_objects 는 가구만).
    """
    poses = extract_model_poses(sdf_path)
    filtered = [
        name for name in poses
        if not any(name.startswith(p) for p in exclude_prefixes)
    ]
    return json.dumps(sorted(filtered))
