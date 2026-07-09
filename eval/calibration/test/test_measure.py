"""measure.py 단위 테스트 — probe positional σ 두 조건 측정 (mock client).

실 OpenAI API 호출 없이 client_factory 주입으로 검증. call_llm 은 호출마다
client_factory() 를 부르므로, 응답 시퀀스를 내는 stateful factory 로 provided /
absent 두 조건의 거동을 결정론적으로 mock 한다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import List, Optional

import pytest
import yaml

from eval_calibration.measure import (
    run_calibration,
    run_probe_calibration,
    save_probe_result,
)
from eval_calibration.schemas import Backbone, ScenarioSpec, TypedAction


# ─── mock OpenAI client (test_llm_client.py 패턴 축약) ────────────────────────
@dataclass
class _MockFunction:
    name: str
    arguments: str


@dataclass
class _MockToolCall:
    function: _MockFunction


@dataclass
class _MockMessage:
    tool_calls: List[_MockToolCall]
    content: Optional[str] = None


@dataclass
class _MockChoice:
    message: _MockMessage


@dataclass
class _MockCompletion:
    choices: List[_MockChoice]


class _MockCompletions:
    def __init__(self, completion):
        self._completion = completion

    def create(self, **kwargs):
        return self._completion


class _MockChat:
    def __init__(self, completion):
        self.completions = _MockCompletions(completion)


class _MockClient:
    def __init__(self, completion):
        self.chat = _MockChat(completion)


def _move_completion(position) -> _MockCompletion:
    args = json.dumps({'position': list(position), 'max_speed': 1.0})
    return _MockCompletion(
        choices=[_MockChoice(_MockMessage(
            tool_calls=[_MockToolCall(_MockFunction('move_to', args))]))]
    )


def _sigma_completion(sigma: str, theta: dict) -> _MockCompletion:
    return _MockCompletion(
        choices=[_MockChoice(_MockMessage(
            tool_calls=[_MockToolCall(_MockFunction(sigma, json.dumps(theta)))]))]
    )


class _SeqFactory:
    """호출 순서대로 미리 정한 completion 을 내는 client_factory."""

    def __init__(self, completions: List[_MockCompletion]):
        self._completions = completions
        self.calls = 0

    def __call__(self):
        comp = self._completions[self.calls]
        self.calls += 1
        return _MockClient(comp)


def _probe_scenario() -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id='ST',
        description='probe test',
        user_prompt='내 머그컵 어디 있어?',
        expected_action=TypedAction('move_to', {'position': [2.0, -1.0, 0.375]}),
        expected_position=(2.0, -1.0, 0.375),
        known_objects=['dining_table'],
        known_object_positions={'dining_table': (2.0, -1.0, 0.375)},
        move_to_probes=[{'prompt': '식탁으로', 'expected_object': 'dining_table'}],
    )


class TestRunProbeCalibration:
    def test_two_conditions_provided_zero_absent_nonzero(self):
        # provided 3회 = 고정 좌표 (σ=0), absent 3회 = 변동 좌표 (σ>0).
        # 측정 순서: provided 먼저, absent 나중.
        provided = [_move_completion((2.0, -1.0, 0.375))] * 3
        absent = [
            _move_completion((1.2, -0.5, 0.4)),
            _move_completion((2.5, -3.4, 0.4)),
            _move_completion((1.8, -1.7, 0.4)),
        ]
        factory = _SeqFactory(provided + absent)

        result = run_probe_calibration(
            Backbone.GPT_4O, _probe_scenario(), n_samples=3,
            client_factory=factory, verbose=False,
        )

        assert factory.calls == 6
        assert len(result.probes) == 1
        p = result.probes[0]
        assert p.expected_object == 'dining_table'
        assert p.expected_xy == (2.0, -1.0)

        # provided: 고정 좌표 → xy σ = 0
        assert p.provided.n_move_to == 3
        assert p.provided.skill_distribution == {'move_to': 3}
        assert p.provided.axis_sigma_cm['x'] == pytest.approx(0.0)
        assert p.provided.axis_sigma_cm['y'] == pytest.approx(0.0)
        assert p.provided.axis_mean_m['x'] == pytest.approx(2.0)

        # absent: 변동 좌표 → xy σ > 0
        assert p.absent.n_move_to == 3
        assert p.absent.axis_sigma_cm['x'] > 0.0
        assert p.absent.axis_sigma_cm['y'] > 0.0

    def test_non_move_to_counted_in_skills_only(self):
        # provided 에 ask_user 1 섞임 → n_move_to=2, skill 분포에 ask_user.
        provided = [
            _move_completion((2.0, -1.0, 0.375)),
            _sigma_completion('ask_user', {'question': '어느 것?'}),
            _move_completion((2.0, -1.0, 0.375)),
        ]
        absent = [_move_completion((1.0, -1.0, 0.4))] * 3
        factory = _SeqFactory(provided + absent)

        result = run_probe_calibration(
            Backbone.GPT_4O, _probe_scenario(), n_samples=3,
            client_factory=factory, verbose=False,
        )
        p = result.probes[0]
        assert p.provided.n_move_to == 2
        assert p.provided.skill_distribution == {'move_to': 2, 'ask_user': 1}
        # 2 표본 동일 좌표 → σ=0 (n>=2 라 NaN 아님)
        assert p.provided.axis_sigma_cm['x'] == pytest.approx(0.0)

    def test_missing_move_to_probes_raises(self):
        s = _probe_scenario()
        s_no_probe = ScenarioSpec(
            scenario_id=s.scenario_id, description=s.description,
            user_prompt=s.user_prompt,
            known_objects=s.known_objects,
            known_object_positions=s.known_object_positions,
            move_to_probes=[],
        )
        with pytest.raises(ValueError, match='move_to_probes'):
            run_probe_calibration(Backbone.GPT_4O, s_no_probe, n_samples=3)

    def test_missing_known_object_positions_raises(self):
        s = _probe_scenario()
        s_no_pos = ScenarioSpec(
            scenario_id=s.scenario_id, description=s.description,
            user_prompt=s.user_prompt,
            known_objects=s.known_objects,
            known_object_positions={},
            move_to_probes=s.move_to_probes,
        )
        with pytest.raises(ValueError, match='known_object_positions'):
            run_probe_calibration(Backbone.GPT_4O, s_no_pos, n_samples=3)


class TestSaveProbeResult:
    def test_roundtrip(self, tmp_path):
        factory = _SeqFactory(
            [_move_completion((2.0, -1.0, 0.375))] * 3
            + [_move_completion((1.0, -1.0, 0.4)),
               _move_completion((2.0, -2.0, 0.4)),
               _move_completion((1.5, -1.5, 0.4))]
        )
        result = run_probe_calibration(
            Backbone.GPT_4O, _probe_scenario(), n_samples=3,
            client_factory=factory, verbose=False,
        )
        path = save_probe_result(result, tmp_path)
        assert path.exists()
        assert path.name.startswith('gpt_4o_2024_05_13_ST_probe_n3_')

        with path.open(encoding='utf-8') as f:
            loaded = yaml.safe_load(f)
        assert loaded['backbone'] == Backbone.GPT_4O.value
        assert loaded['scenario'] == 'ST'
        assert loaded['n_samples'] == 3
        assert len(loaded['probes']) == 1
        probe = loaded['probes'][0]
        assert probe['expected_object'] == 'dining_table'
        # tuple → list (yaml safe_dump)
        assert probe['expected_xy'] == [2.0, -1.0]
        assert probe['provided']['axis_sigma_cm']['x'] == pytest.approx(0.0)
        assert probe['absent']['axis_sigma_cm']['x'] > 0.0


class TestRunCalibrationRegression:
    """기존 run_calibration (단일 user_prompt 거동 분포) mock 회귀."""

    def test_move_to_position_delta(self):
        # expected_position=(2.0,-1.0,0.375). 3회 모두 정확 좌표 → σ_pos = 0.
        factory = _SeqFactory([_move_completion((2.0, -1.0, 0.375))] * 3)
        result = run_calibration(
            Backbone.GPT_4O, _probe_scenario(), n_samples=3,
            client_factory=factory, verbose=False,
        )
        assert len(result.samples) == 3
        # 모두 expected 와 일치 → position_xyz_cm 의 std = 0
        assert result.sigma_llm_nat.position_xyz_cm == pytest.approx(0.0)
        assert result.sigma_llm_nat.no_call_rate == 0.0
