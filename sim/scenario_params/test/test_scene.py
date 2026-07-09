"""scenario_params.scene + scenario_location 단위 테스트.

장소별 장면 객체 (context augmentation 데이터 소스) + scenario→location 매핑
단일 진실 소스 검증.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenario_params.params import (
    SCENARIO_LOCATION,
    VALID_SCENARIOS,
    scenario_location,
)
from scenario_params.scene import (
    OVD_CLASS_SYNONYMS,
    VALID_LOCATIONS,
    expand_ovd_synonyms,
    ovd_vocabulary_all,
    ovd_vocabulary_for_location,
    ovd_vocabulary_launch_str,
    scene_objects_for_location,
)


class TestScenarioLocation:
    def test_livingroom_scenarios(self) -> None:
        # ADR-0039 D2: 본실험 scenario_id = 거실 S5/S6 만 (S7 폐기·S8 paper-2 이관).
        # yard location 자체는 scene 에 보존(paper-2 인프라)되나 scenario_id 매핑엔 없음.
        for sid in ('S5', 'S6'):
            assert scenario_location(sid) == 'livingroom'

    def test_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError):
            scenario_location('S3')

    def test_locations_subset_of_scene(self) -> None:
        """모든 scenario_location 값이 scene 정의 장소에 존재."""
        assert set(SCENARIO_LOCATION.values()) <= set(VALID_LOCATIONS)

    def test_locations_match_sim_scenarios(self) -> None:
        """SCENARIO_LOCATION 의 location 값이 sim 장소(VALID_SCENARIOS)와 정합."""
        assert set(SCENARIO_LOCATION.values()) <= set(VALID_SCENARIOS)


class TestSceneObjects:
    def test_livingroom_has_furniture(self) -> None:
        names = {o['name'] for o in scene_objects_for_location('livingroom')}
        assert {'sofa', 'coffee_table', 'tv', 'dining_table', 'dock'} <= names

    def test_yard_has_people_and_dock(self) -> None:
        names = {o['name'] for o in scene_objects_for_location('yard')}
        assert 'child_red_shirt' in names
        assert 'dock' in names

    def test_objects_have_xyz_position(self) -> None:
        for loc in VALID_LOCATIONS:
            for obj in scene_objects_for_location(loc):
                assert 'name' in obj
                assert len(obj['position']) == 3
                assert all(isinstance(c, (int, float)) for c in obj['position'])

    # ADR-0029 블로커 1 — 객체별 OVD 클래스 라벨 (인스턴스 id ↔ 검출 클래스 입도 해소).
    def test_objects_have_ovd_class_key(self) -> None:
        for loc in VALID_LOCATIONS:
            for obj in scene_objects_for_location(loc):
                assert 'ovd_class' in obj
                assert obj['ovd_class'] is None or isinstance(obj['ovd_class'], str)

    def test_chair_instances_map_to_chair_class(self) -> None:
        objs = {o['name']: o['ovd_class']
                for o in scene_objects_for_location('livingroom')}
        assert objs['chair_left'] == 'chair'
        assert objs['chair_right'] == 'chair'

    def test_table_instances_map_to_table_class(self) -> None:
        objs = {o['name']: o['ovd_class']
                for o in scene_objects_for_location('livingroom')}
        assert objs['coffee_table'] == 'table'
        assert objs['dining_table'] == 'table'

    def test_s5_mugs_map_to_cup_class(self) -> None:
        """S5 모호 referent 머그컵 3개 = 'cup' 클래스 (ADR-0035)."""
        objs = {o['name']: o['ovd_class']
                for o in scene_objects_for_location('livingroom')}
        assert objs['mug_left'] == 'cup'
        assert objs['mug_center'] == 'cup'
        assert objs['mug_right'] == 'cup'

    def test_s5_mug_positions_match_sdf(self) -> None:
        """머그컵 world 좌표 = livingroom_base.sdf / calibration ground truth (ADR-0035)."""
        pos = {o['name']: o['position']
               for o in scene_objects_for_location('livingroom')}
        assert pos['mug_left'] == [1.7, -1.0, 0.80]
        assert pos['mug_center'] == [2.0, -1.0, 0.80]
        assert pos['mug_right'] == [2.3, -1.0, 0.80]

    def test_vocab_absent_objects_have_none_class(self) -> None:
        objs = {o['name']: o['ovd_class']
                for o in scene_objects_for_location('livingroom')}
        assert objs['tv_stand'] is None
        assert objs['dock'] is None

    def test_yard_people_map_to_person_class(self) -> None:
        for o in scene_objects_for_location('yard'):
            if o['name'].startswith(('child', 'adult')):
                assert o['ovd_class'] == 'person'

    def test_sofa_position_matches_sdf(self) -> None:
        """거실 sofa world 좌표 = livingroom_base.sdf 정합 (v4.1 layout — sofa 원위치)."""
        sofa = next(
            o for o in scene_objects_for_location('livingroom')
            if o['name'] == 'sofa'
        )
        assert sofa['position'] == [-1.8, 1.5, 0.4]

    def test_unknown_location_raises(self) -> None:
        with pytest.raises(RuntimeError):
            scene_objects_for_location('kitchen')

    def test_returns_fresh_copy(self) -> None:
        """caller mutation 격리 — 반환 list/dict 수정이 원본 미영향."""
        objs = scene_objects_for_location('livingroom')
        objs[0]['position'][0] = 999.0
        objs2 = scene_objects_for_location('livingroom')
        assert objs2[0]['position'][0] != 999.0


class TestOvdVocabulary:
    """OVD 정적 vocabulary 파생 (scene ``ovd_class`` 단일 소스) — 세션 53 B4 게이트
    e2e 가 적발한 vocab↔referent drift(거실 'sofa'·마당 'person' 누락) 차단."""

    def test_livingroom_vocab_from_scene(self) -> None:
        assert ovd_vocabulary_for_location('livingroom') == ['chair', 'cup', 'sofa', 'table']

    def test_yard_vocab_from_scene(self) -> None:
        assert ovd_vocabulary_for_location('yard') == ['person']

    def test_vocab_excludes_none_class_objects(self) -> None:
        """어휘 밖 객체(tv_stand·tv·dock = ovd_class None)는 어휘에 미포함."""
        vocab = ovd_vocabulary_for_location('livingroom')
        assert 'dock' not in vocab
        assert None not in vocab

    def test_vocab_sorted_unique(self) -> None:
        """동일 클래스 다수 인스턴스(chair_left/right)는 1회만, 정렬."""
        for loc in VALID_LOCATIONS:
            vocab = ovd_vocabulary_for_location(loc)
            assert vocab == sorted(set(vocab))

    def test_vocab_all_is_union(self) -> None:
        """전 장소 합집합 = 영속 OVD detector(전 시나리오 서빙)용."""
        expected: set = set()
        for loc in VALID_LOCATIONS:
            expected.update(ovd_vocabulary_for_location(loc))
        assert ovd_vocabulary_all() == sorted(expected)

    def test_vocab_all_covers_each_location(self) -> None:
        for loc in VALID_LOCATIONS:
            assert set(ovd_vocabulary_for_location(loc)) <= set(ovd_vocabulary_all())

    def test_vocab_all_canonical_value(self) -> None:
        """scripts fallback(up.sh·start_intent_stack.sh)이 박는 합집합과 정합."""
        assert ovd_vocabulary_all() == ['chair', 'cup', 'person', 'sofa', 'table']

    def test_launch_str_all(self) -> None:
        assert ovd_vocabulary_launch_str() == "['chair','cup','person','sofa','table']"

    def test_launch_str_per_location(self) -> None:
        assert ovd_vocabulary_launch_str('livingroom') == "['chair','cup','sofa','table']"
        assert ovd_vocabulary_launch_str('yard') == "['person']"


class TestOvdSynonyms:
    """OVD 어휘 동의어 정규화 단일 소스 (세션 62 — llama σ target_id='mug')."""

    def test_mapping_targets_are_canonical_vocab(self) -> None:
        """동의어 표의 정본(값)은 반드시 실제 OVD 어휘 안에 있어야 함 — drift 차단."""
        vocab = set(ovd_vocabulary_all())
        for synonym, canon in OVD_CLASS_SYNONYMS.items():
            assert canon in vocab
            # 동의어(키)가 어휘 자체면 매핑이 무의미 — 표 오염 차단.
            assert synonym not in vocab

    def test_expand_adds_canonical(self) -> None:
        assert expand_ovd_synonyms({'mug'}) == {'mug', 'cup'}
        assert expand_ovd_synonyms({'couch'}) == {'couch', 'sofa'}

    def test_expand_preserves_input(self) -> None:
        """비동의어 원소는 그대로 보존 (원소 제거 없음)."""
        labels = {'chair', 'mug_cup', 'mug'}
        out = expand_ovd_synonyms(labels)
        assert labels <= out and 'cup' in out

    def test_expand_noop_without_synonyms(self) -> None:
        assert expand_ovd_synonyms({'chair', 'sofa'}) == {'chair', 'sofa'}
        assert expand_ovd_synonyms(set()) == set()

    def test_launch_str_unknown_location_raises(self) -> None:
        with pytest.raises(RuntimeError):
            ovd_vocabulary_launch_str('kitchen')
