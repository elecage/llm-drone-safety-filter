"""sdf_targets.py — SDF model pose 추출 단위 테스트."""

from __future__ import annotations

import json
import textwrap

import pytest

from tier2_gate.sdf_targets import (
    extract_known_objects_json,
    extract_model_poses,
    extract_model_poses_json,
)


# 인라인 SDF — 실제 livingroom 구조 모방 (model + 직속 pose).
_SDF_OK = textwrap.dedent("""\
    <?xml version="1.0"?>
    <sdf version="1.10">
      <world name="test">
        <model name="ground">
          <static>true</static>
          <pose>0 0 -0.05 0 0 0</pose>
        </model>
        <model name="wall_north">
          <pose>0 2.05 1.2 0 0 0</pose>
        </model>
        <model name="sofa">
          <pose>-1.8 1.5 0.4 0 0 0</pose>
          <link name="visual_link">
            <pose>0.1 0 0 0 0 0</pose>
          </link>
        </model>
        <model name="tv">
          <pose>2.4 0.0 0.8 0 0 1.5708</pose>
        </model>
        <model name="no_pose_model">
        </model>
      </world>
    </sdf>
""")


@pytest.fixture
def sdf_file(tmp_path):
    p = tmp_path / 'test_world.sdf'
    p.write_text(_SDF_OK, encoding='utf-8')
    return str(p)


# ---- extract_model_poses ----

def test_extract_returns_world_pose_only():
    """model 직속 pose 추출, link 내 pose 무시 — sofa = (-1.8, 1.5, 0.4)."""
    poses = extract_model_poses(_write_to_tmp(_SDF_OK))
    assert poses['sofa'] == (-1.8, 1.5, 0.4)


def test_extract_all_named_models(sdf_file):
    poses = extract_model_poses(sdf_file)
    assert set(poses.keys()) == {'ground', 'wall_north', 'sofa', 'tv'}


def test_extract_skips_model_without_pose(sdf_file):
    poses = extract_model_poses(sdf_file)
    assert 'no_pose_model' not in poses


def test_extract_drops_rotation(sdf_file):
    """yaw (1.5708) 등 회전은 추출 결과에 안 들어감."""
    poses = extract_model_poses(sdf_file)
    assert poses['tv'] == (2.4, 0.0, 0.8)
    assert len(poses['tv']) == 3


def test_extract_missing_file_returns_empty():
    assert extract_model_poses('/nonexistent/path/to/world.sdf') == {}


def test_extract_invalid_xml_returns_empty(tmp_path):
    p = tmp_path / 'bad.sdf'
    p.write_text('<<<not xml>>>', encoding='utf-8')
    assert extract_model_poses(str(p)) == {}


def test_extract_pose_with_non_numeric_skipped(tmp_path):
    """pose 가 'π/2' 같은 non-numeric 이면 해당 model skip."""
    bad = textwrap.dedent("""\
        <?xml version="1.0"?>
        <sdf version="1.10"><world name="t">
          <model name="bad"><pose>foo bar baz 0 0 0</pose></model>
          <model name="good"><pose>1 2 3 0 0 0</pose></model>
        </world></sdf>
    """)
    p = tmp_path / 'mixed.sdf'
    p.write_text(bad, encoding='utf-8')
    poses = extract_model_poses(str(p))
    assert poses == {'good': (1.0, 2.0, 3.0)}


# ---- extract_model_poses_json ----

def test_extract_json_format(sdf_file):
    s = extract_model_poses_json(sdf_file)
    decoded = json.loads(s)
    assert decoded['sofa'] == [-1.8, 1.5, 0.4]
    assert isinstance(decoded['tv'], list) and len(decoded['tv']) == 3


# ---- extract_known_objects_json ----

def test_known_objects_default_includes_all(sdf_file):
    s = extract_known_objects_json(sdf_file)
    decoded = json.loads(s)
    assert set(decoded) == {'ground', 'wall_north', 'sofa', 'tv'}


def test_known_objects_excludes_walls_and_ground(sdf_file):
    s = extract_known_objects_json(
        sdf_file, exclude_prefixes=('wall_', 'ground', 'ceiling')
    )
    decoded = json.loads(s)
    assert set(decoded) == {'sofa', 'tv'}


def test_known_objects_returns_sorted(sdf_file):
    s = extract_known_objects_json(sdf_file)
    decoded = json.loads(s)
    assert decoded == sorted(decoded)


# ---- helper ----

def _write_to_tmp(content: str) -> str:
    """tmp_path fixture 없이 ad-hoc 임시 파일 — module-level constant 한 줄 검증용."""
    import tempfile
    f = tempfile.NamedTemporaryFile(
        mode='w', suffix='.sdf', delete=False, encoding='utf-8'
    )
    f.write(content)
    f.close()
    return f.name
