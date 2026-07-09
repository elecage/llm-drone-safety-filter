"""prompts.py YAML loader 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_calibration.prompts import discover_scenarios, load_scenario


SCENARIOS_DIR = Path(__file__).resolve().parent.parent / 'scenarios'


class TestLoadScenario:
    def test_loads_s5_ambiguous_referent(self):
        path = SCENARIOS_DIR / 'S5_ambiguous_referent.yaml'
        spec = load_scenario(path)
        assert spec.scenario_id == 'S5'
        # S5 README §1 narrative 정합 (PR S5-cup amendment)
        assert '머그컵' in spec.user_prompt
        # 식탁 위 외형 동일 3 mug (S5 README §2.2 A/B/C)
        assert 'mug_left' in spec.known_objects
        assert 'mug_center' in spec.known_objects
        assert 'mug_right' in spec.known_objects
        assert spec.expected_action is not None
        assert spec.expected_action.sigma == 'ask_user'

    def test_loads_s6_adversarial_setpoint(self):
        path = SCENARIOS_DIR / 'S6_adversarial_setpoint.yaml'
        spec = load_scenario(path)
        assert spec.scenario_id == 'S6'
        assert '식탁' in spec.user_prompt
        assert spec.expected_action is not None
        assert spec.expected_action.sigma == 'move_to'
        # ADR-0013 D2 move_to.theta = {position, max_speed}
        assert spec.expected_action.theta['position'] == [2.0, -1.0, 1.25]
        assert spec.expected_action.theta['max_speed'] == 0.3
        assert spec.expected_position == (2.0, -1.0, 1.25)
        assert spec.expected_target_id is None
        assert 'book_on_dining_table' in spec.known_objects

    def test_loads_s7_cognitive_lapse(self):
        path = SCENARIOS_DIR / 'S7_cognitive_lapse.yaml'
        spec = load_scenario(path)
        assert spec.scenario_id == 'S7'
        assert '거실 탁자' in spec.user_prompt
        assert spec.expected_action is not None
        assert spec.expected_action.sigma == 'inspect'
        # ADR-0013 D2 inspect.theta = {target_id, viewpoint}
        assert spec.expected_action.theta['target_id'] == 'book_on_coffee_table'
        assert spec.expected_action.theta['viewpoint'] == 'close'
        assert spec.expected_position is None  # inspect 는 position 미사용
        assert spec.expected_target_id == 'book_on_coffee_table'
        assert 'book_on_coffee_table' in spec.known_objects

    def test_loads_s8_crowd_cinematography(self):
        path = SCENARIOS_DIR / 'S8_crowd_cinematography.yaml'
        spec = load_scenario(path)
        assert spec.scenario_id == 'S8'
        assert '빨간 셔츠' in spec.user_prompt
        assert spec.expected_action is not None
        assert spec.expected_action.sigma == 'move_to'
        assert spec.expected_action.theta['position'] == [1.0, 1.0, 1.5]
        assert spec.expected_position == (1.0, 1.0, 1.5)
        assert spec.expected_target_id == 'child_red_shirt'
        # OVD attribute_mismatch fault 측 distractor 가 known_objects 안에 포함
        assert 'child_red_shirt' in spec.known_objects
        assert 'adult_red_hat' in spec.known_objects

    def test_missing_required_keys_rejected(self, tmp_path):
        bad = tmp_path / 'bad.yaml'
        bad.write_text('scenario_id: BAD\n')  # description / user_prompt 누락
        with pytest.raises(ValueError, match='필수 키'):
            load_scenario(bad)

    def test_invalid_expected_position_rejected(self, tmp_path):
        bad = tmp_path / 'bad.yaml'
        bad.write_text(
            'scenario_id: X\n'
            'description: x\n'
            'user_prompt: x\n'
            'expected_position: [1, 2]\n'  # 2-tuple, 3 아님
        )
        with pytest.raises(ValueError, match='3-tuple'):
            load_scenario(bad)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match='시나리오 YAML 없음'):
            load_scenario(tmp_path / 'nonexistent.yaml')


class TestDerivedFieldConsistency:
    """PR #86 review R1 — expected_position / expected_target_id 는 derived.

    source of truth = expected_action.theta. 두 값이 어긋나면 ValueError.
    누락 시 자동 fill.
    """

    def _yaml(self, sigma: str, theta: dict, **extra) -> str:
        import yaml as _yaml
        body = {
            'scenario_id': 'TEST',
            'description': 'test',
            'user_prompt': 'test',
            'expected_action': {'sigma': sigma, 'theta': theta},
            **extra,
        }
        return _yaml.safe_dump(body, allow_unicode=True)

    def test_expected_position_autofilled_from_theta(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml('move_to', {'position': [1.0, 2.0, 3.0], 'max_speed': 0.3}))
        spec = load_scenario(f)
        # YAML 에 expected_position 명시 X — theta 측에서 auto-fill
        assert spec.expected_position == (1.0, 2.0, 3.0)

    def test_expected_position_explicit_match_ok(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'move_to', {'position': [1.0, 2.0, 3.0], 'max_speed': 0.3},
            expected_position=[1.0, 2.0, 3.0],
        ))
        spec = load_scenario(f)
        assert spec.expected_position == (1.0, 2.0, 3.0)

    def test_expected_position_mismatch_rejected(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'move_to', {'position': [1.0, 2.0, 3.0], 'max_speed': 0.3},
            expected_position=[1.0, 2.0, 9.9],  # 불일치
        ))
        with pytest.raises(ValueError, match='source of truth = theta'):
            load_scenario(f)

    def test_expected_target_id_autofilled_from_theta(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'inspect', {'target_id': 'book_on_coffee_table', 'viewpoint': 'close'},
        ))
        spec = load_scenario(f)
        assert spec.expected_target_id == 'book_on_coffee_table'

    def test_expected_target_id_mismatch_rejected(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'inspect', {'target_id': 'book_on_coffee_table', 'viewpoint': 'close'},
            expected_target_id='something_else',  # 불일치
        ))
        with pytest.raises(ValueError, match='source of truth = theta'):
            load_scenario(f)

    def test_ask_user_no_derived_fields(self, tmp_path):
        """sigma=ask_user 는 position·target_id 둘 다 N/A — explicit None 통과."""
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml('ask_user', {'question': 'which?'}))
        spec = load_scenario(f)
        assert spec.expected_position is None
        assert spec.expected_target_id is None

    def test_real_scenarios_consistency(self):
        """기존 4 시나리오 모두 derived field 정합성 통과."""
        scenarios = discover_scenarios(SCENARIOS_DIR)
        # S6/S8 move_to 는 expected_position 이 theta.position 과 일치
        assert scenarios['S6'].expected_position == (2.0, -1.0, 1.25)
        assert scenarios['S8'].expected_position == (1.0, 1.0, 1.5)
        # S7 inspect 는 expected_target_id 가 theta.target_id 와 일치
        assert scenarios['S7'].expected_target_id == 'book_on_coffee_table'
        # S5 ask_user 는 둘 다 None
        assert scenarios['S5'].expected_position is None
        assert scenarios['S5'].expected_target_id is None
        # S8 dual purpose — sigma=move_to 인데 expected_target_id 명시 (R-2nd-A 정합)
        assert scenarios['S8'].expected_target_id == 'child_red_shirt'


class TestSigmaCompatibilityValidation:
    """PR #87 review R-2nd-A — sigma 비호환 explicit field 검증.

    derived field 모두 *완전 부재* 인 sigma (ask_user/return_to_dock/
    emergency_land) 에서 expected_position·expected_target_id 명시는 silent
    pass 가 silent 측정 오류 위험 → ValueError 강제. move_to/inspect 사이의
    dual purpose (예 S8 의 sigma=move_to + expected_target_id) 는 허용.
    """

    def _yaml(self, sigma: str, theta: dict, **extra) -> str:
        import yaml as _yaml
        body = {
            'scenario_id': 'TEST',
            'description': 'test',
            'user_prompt': 'test',
            'expected_action': {'sigma': sigma, 'theta': theta},
            **extra,
        }
        return _yaml.safe_dump(body, allow_unicode=True)

    def test_ask_user_with_position_rejected(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'ask_user', {'question': 'which?'},
            expected_position=[1.0, 2.0, 3.0],  # 비호환
        ))
        with pytest.raises(ValueError, match='허용 안 됨'):
            load_scenario(f)

    def test_ask_user_with_target_id_rejected(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'ask_user', {'question': 'which?'},
            expected_target_id='something',  # 비호환
        ))
        with pytest.raises(ValueError, match='허용 안 됨'):
            load_scenario(f)

    def test_return_to_dock_with_position_rejected(self, tmp_path):
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'return_to_dock', {},
            expected_position=[0.0, 0.0, 0.0],
        ))
        with pytest.raises(ValueError, match='허용 안 됨'):
            load_scenario(f)

    def test_move_to_with_target_id_allowed_dual_purpose(self, tmp_path):
        """S8 패턴 — sigma=move_to + expected_target_id (fault injection ref).

        analyze.py 의 target_swap_rate 가 inspect 측 LLM σ 만 보므로
        sigma=move_to 시 expected_target_id 가 silent 오류 위험 없음.
        """
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'move_to', {'position': [1.0, 1.0, 1.5], 'max_speed': 0.3},
            expected_target_id='child_red_shirt',
        ))
        spec = load_scenario(f)
        assert spec.expected_position == (1.0, 1.0, 1.5)
        assert spec.expected_target_id == 'child_red_shirt'

    def test_inspect_with_position_allowed_dual_purpose(self, tmp_path):
        """inspect.viewpoint 좌표가 SDF 측 도출값일 수 있음 — explicit 허용."""
        f = tmp_path / 's.yaml'
        f.write_text(self._yaml(
            'inspect', {'target_id': 'book_x', 'viewpoint': 'close'},
            expected_position=[1.0, 2.0, 3.0],  # dual purpose
        ))
        spec = load_scenario(f)
        assert spec.expected_position == (1.0, 2.0, 3.0)
        assert spec.expected_target_id == 'book_x'


class TestDiscoverScenarios:
    def test_discovers_paper_c_four_scenarios(self):
        """ADR-0025 D3 amendment 7 + ADR-0026 D6 — paper §C 시뮬 indoor 4 시나리오.

        S3 (지붕, 실외) 는 paper §C 범위 밖 (ADR-0006 D1 + ADR-0026 D6 정합).
        """
        scenarios = discover_scenarios(SCENARIOS_DIR)
        assert set(scenarios.keys()) == {'S5', 'S6', 'S7', 'S8'}
        for sid in ('S5', 'S6', 'S7', 'S8'):
            assert scenarios[sid].user_prompt
            assert scenarios[sid].description
            assert scenarios[sid].known_objects

    def test_expected_action_sigma_in_adr0013_catalog(self):
        """ADR-0013 D2 5 카탈로그 강제 — schemas.TypedAction validator 위임."""
        scenarios = discover_scenarios(SCENARIOS_DIR)
        allowed = {'move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'}
        for sid, spec in scenarios.items():
            assert spec.expected_action is not None, sid
            assert spec.expected_action.sigma in allowed, sid

    def test_directory_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match='scenarios 디렉터리'):
            discover_scenarios(tmp_path / 'no_such_dir')

    def test_duplicate_scenario_id_rejected(self, tmp_path):
        (tmp_path / 'a.yaml').write_text(
            'scenario_id: S5\ndescription: x\nuser_prompt: x\n'
        )
        (tmp_path / 'b.yaml').write_text(
            'scenario_id: S5\ndescription: y\nuser_prompt: y\n'
        )
        with pytest.raises(ValueError, match='중복 scenario_id'):
            discover_scenarios(tmp_path)


class TestKnownObjectPositions:
    """ADR-0025 amend 12 (D1.e) — context-provided 좌표 데이터화."""

    def test_s6_has_positions(self) -> None:
        s = load_scenario(SCENARIOS_DIR / 'S6_adversarial_setpoint.yaml')
        assert len(s.known_object_positions) == 10
        assert s.known_object_positions['dining_table'] == (2.0, -1.0, 0.375)

    def test_positions_are_tuples(self) -> None:
        s = load_scenario(SCENARIOS_DIR / 'S6_adversarial_setpoint.yaml')
        for pos in s.known_object_positions.values():
            assert isinstance(pos, tuple) and len(pos) == 3

    def test_missing_positions_empty_dict(self, tmp_path) -> None:
        # known_object_positions 미정의 시나리오 → 빈 dict (context-absent only)
        (tmp_path / 's.yaml').write_text(
            'scenario_id: S5\ndescription: x\nuser_prompt: x\n'
        )
        s = load_scenario(tmp_path / 's.yaml')
        assert s.known_object_positions == {}

    def test_bad_position_raises(self, tmp_path) -> None:
        (tmp_path / 'bad.yaml').write_text(
            'scenario_id: S5\ndescription: x\nuser_prompt: x\n'
            'known_object_positions:\n  tv: [1.0, 2.0]\n'
        )
        with pytest.raises(ValueError, match='3-tuple'):
            load_scenario(tmp_path / 'bad.yaml')


class TestMoveToProbes:
    """ADR-0025 amend 13 — positional σ 측정용 move_to probe 발화."""

    def test_s6_has_probes(self) -> None:
        s = load_scenario(SCENARIOS_DIR / 'S6_adversarial_setpoint.yaml')
        assert len(s.move_to_probes) >= 1
        for p in s.move_to_probes:
            assert 'prompt' in p and 'expected_object' in p
            # expected_object 는 known_object_positions 키여야 (xy 기준 유효)
            assert p['expected_object'] in s.known_object_positions

    def test_missing_probes_empty(self, tmp_path) -> None:
        (tmp_path / 's.yaml').write_text(
            'scenario_id: S5\ndescription: x\nuser_prompt: x\n'
        )
        s = load_scenario(tmp_path / 's.yaml')
        assert s.move_to_probes == []

    def test_bad_probe_raises(self, tmp_path) -> None:
        (tmp_path / 'bad.yaml').write_text(
            'scenario_id: S5\ndescription: x\nuser_prompt: x\n'
            'move_to_probes:\n  - prompt: 가줘\n'  # expected_object 누락
        )
        with pytest.raises(ValueError, match='expected_object'):
            load_scenario(tmp_path / 'bad.yaml')
