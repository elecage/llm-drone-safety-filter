"""injector_helpers.py 단위 테스트 — JSON ser/de round-trip + edge.

rclpy 의존성 *없이* host venv 측 통과. injector_node (rclpy 측 노드 자체)
는 colcon test 측 분리.
"""

from __future__ import annotations

import json

import pytest

from eval_calibration.schemas import TypedAction

from eval_faults.injector_helpers import (
    lapse_event_from_json,
    lapse_event_to_json,
    typed_action_from_json,
    typed_action_to_json,
)
from eval_faults.schemas import (
    CognitiveLapseVariant,
    LapseEvent,
)


# ----------------------------------------------------------- TypedAction round-trip


class TestTypedActionJson:
    def test_round_trip_move_to(self):
        action = TypedAction(
            sigma='move_to',
            theta={'position': [1.0, 2.0, 3.0], 'frame': 'world'},
        )
        s = typed_action_to_json(action)
        restored = typed_action_from_json(s)
        assert restored == action

    def test_round_trip_inspect(self):
        action = TypedAction(
            sigma='inspect',
            theta={'target_id': 'book_living_table', 'viewpoint': 'close'},
        )
        s = typed_action_to_json(action)
        restored = typed_action_from_json(s)
        assert restored == action

    def test_from_json_invalid_sigma_rejected(self):
        s = json.dumps({'sigma': 'not_a_skill', 'theta': {}})
        with pytest.raises(ValueError):
            typed_action_from_json(s)

    def test_from_json_missing_key_raises(self):
        s = json.dumps({'sigma': 'move_to'})  # theta 부재
        with pytest.raises(KeyError):
            typed_action_from_json(s)

    def test_from_json_non_dict_rejected(self):
        with pytest.raises(ValueError, match='JSON root'):
            typed_action_from_json(json.dumps(['list', 'not', 'dict']))


# ----------------------------------------------------------- LapseEvent round-trip


class TestLapseEventJson:
    def test_round_trip_e1_self_correction(self):
        event = LapseEvent(
            variant=CognitiveLapseVariant.E1_SELF_CORRECTION,
            trigger_time_s=10.5,
            initial_utterance='거실 탁자 위 책 보여줘.',
            follow_up_utterance='아니, 식탁 위 머그컵 보여줘.',
            silence_threshold_s=None,
            raw_c_after_event=0.91,
        )
        s = lapse_event_to_json(event)
        restored = lapse_event_from_json(s)
        assert restored == event

    def test_round_trip_e4_utterance_cut(self):
        event = LapseEvent(
            variant=CognitiveLapseVariant.E4_UTTERANCE_CUT,
            trigger_time_s=15.0,
            initial_utterance='거실 탁자 위 책 보여줘.',
            follow_up_utterance=None,
            silence_threshold_s=12.0,
            raw_c_after_event=None,
        )
        s = lapse_event_to_json(event)
        restored = lapse_event_from_json(s)
        assert restored == event

    def test_from_json_invalid_variant_rejected(self):
        s = json.dumps({
            'variant': 'not_a_variant',
            'trigger_time_s': 5.0,
            'initial_utterance': 'hello',
            'follow_up_utterance': 'follow up',
            'silence_threshold_s': None,
            'raw_c_after_event': 0.9,
        })
        with pytest.raises(ValueError):
            lapse_event_from_json(s)

    def test_from_json_invariant_violation_rejected(self):
        """LapseEvent invariant (E4 ↔ follow_up=None) 측 violation."""
        s = json.dumps({
            'variant': 'E4_utterance_cut',
            'trigger_time_s': 5.0,
            'initial_utterance': 'hello',
            'follow_up_utterance': 'should be None',  # E4 측 None 강제
            'silence_threshold_s': 10.0,
            'raw_c_after_event': None,
        })
        with pytest.raises(ValueError, match='E4_utterance_cut'):
            lapse_event_from_json(s)

    def test_from_json_non_dict_rejected(self):
        with pytest.raises(ValueError, match='JSON root'):
            lapse_event_from_json(json.dumps(['list']))
