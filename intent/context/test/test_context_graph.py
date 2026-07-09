"""intent_context.context_graph 단위 테스트 — 장면 조립 순수 로직 (host venv).

scenario → context graph dict (장소 + 사용자 위치 + 객체) + 직렬화 + wrapper
parse 계약 호환 검증.
"""

from __future__ import annotations

import json

import pytest

from intent_context.context_graph import (
    build_context_graph,
    serialize_context_graph,
)


class TestBuildContextGraph:
    def test_livingroom_scenario(self) -> None:
        g = build_context_graph('S5')
        assert g['scenario'] == 'S5'
        assert g['location'] == 'livingroom'
        assert len(g['user_position']) == 3
        names = {o['name'] for o in g['objects']}
        assert {'sofa', 'tv', 'dock'} <= names

    def test_yard_scenario(self) -> None:
        g = build_context_graph('S8')
        assert g['location'] == 'yard'
        names = {o['name'] for o in g['objects']}
        assert 'child_red_shirt' in names

    def test_user_position_from_scenario_params(self) -> None:
        # livingroom user world = (0.0, 1.5, 1.1) (scenario_params v4.1 layout 2026-05-30)
        g = build_context_graph('S6')
        assert g['user_position'] == [0.0, 1.5, 1.1]

    def test_objects_have_position(self) -> None:
        for obj in build_context_graph('S7')['objects']:
            assert len(obj['position']) == 3

    def test_unknown_scenario_raises(self) -> None:
        with pytest.raises(RuntimeError):
            build_context_graph('S3')

    def test_drone_position_omitted_by_default(self) -> None:
        """drone_world_position 미지정 → 결과 dict 에 drone_position 키 없음."""
        g = build_context_graph('S5')
        assert 'drone_position' not in g

    def test_drone_position_included_when_provided(self) -> None:
        g = build_context_graph('S5', drone_world_position=[0.5, -0.5, 1.65])
        assert g['drone_position'] == [0.5, -0.5, 1.65]

    def test_drone_position_coerced_to_floats(self) -> None:
        g = build_context_graph('S5', drone_world_position=(1, 2, 3))
        assert g['drone_position'] == [1.0, 2.0, 3.0]
        assert all(isinstance(v, float) for v in g['drone_position'])

    def test_drone_position_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError):
            build_context_graph('S5', drone_world_position=[1.0, 2.0])


class TestSerializeContextGraph:
    def test_round_trip(self) -> None:
        g = build_context_graph('S5')
        data = json.loads(serialize_context_graph(g))
        assert data['scenario'] == 'S5'
        assert data['location'] == 'livingroom'
        assert isinstance(data['objects'], list)

    def test_root_is_dict_for_wrapper_parse(self) -> None:
        """wrapper_payload.parse_context_graph 는 JSON root=dict 요구 — 호환 확인."""
        data = json.loads(serialize_context_graph(build_context_graph('S8')))
        assert isinstance(data, dict)

    def test_korean_safe(self) -> None:
        # ensure_ascii=False — 한글 객체명 도입 시 깨지지 않음 (현재 영문이나 계약 고정)
        s = serialize_context_graph(build_context_graph('S5'))
        assert isinstance(s, str)
