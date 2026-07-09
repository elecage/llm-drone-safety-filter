"""detection_bridge 단위 테스트 — Detection2DArray ↔ 내부 Detection 변환.

순수 변환(`detection2d_array_to_internal` + bbox 수학)을 host venv 에서 검증.
``vision_msgs`` 메시지는 duck-typed mock(SimpleNamespace)으로 대체 —
`internal_to_detection2d_array`(메시지 생성)는 ROS 2 환경 전용이라 Docker colcon
(injector 콜백 e2e)에서 별도 검증.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eval_faults.detection_bridge import (
    center_size_to_xyxy,
    detection2d_array_to_internal,
    xyxy_to_center_size,
)
from eval_faults.schemas import Detection


# ---------------------------------------------------------------- bbox 수학


def test_xyxy_center_size_round_trip():
    """xyxy → center+size → xyxy 항등 (detector_node 규약 왕복)."""
    x1, y1, x2, y2 = 10.0, 20.0, 30.0, 50.0
    cx, cy, w, h = xyxy_to_center_size(x1, y1, x2, y2)
    assert (cx, cy, w, h) == (20.0, 35.0, 20.0, 30.0)
    back = center_size_to_xyxy(cx, cy, w, h)
    assert back == pytest.approx((x1, y1, x2, y2))


def test_center_size_to_xyxy_known():
    """center+size → corner — detector_node `_to_detection2d` 역변환 일치."""
    assert center_size_to_xyxy(20.0, 35.0, 20.0, 30.0) == pytest.approx(
        (10.0, 20.0, 30.0, 50.0)
    )


# ---------------------------------------------------------------- mock 메시지


def _mk_det2d(label, score, cx, cy, w, h, *, with_hyp=True):
    """duck-typed vision_msgs/Detection2D mock."""
    bbox = SimpleNamespace(
        center=SimpleNamespace(position=SimpleNamespace(x=cx, y=cy), theta=0.0),
        size_x=w, size_y=h,
    )
    results = []
    if with_hyp:
        results.append(SimpleNamespace(
            hypothesis=SimpleNamespace(class_id=label, score=score),
        ))
    return SimpleNamespace(bbox=bbox, results=results)


def _mk_arr(*dets):
    return SimpleNamespace(detections=list(dets), header=SimpleNamespace())


# ---------------------------------------------------------------- array → internal


def test_array_to_internal_single():
    """단일 detection — label·score·bbox(xyxy) 정확 변환."""
    arr = _mk_arr(_mk_det2d('chair', 0.74, 20.0, 35.0, 20.0, 30.0))
    out = detection2d_array_to_internal(arr)
    assert len(out) == 1
    det = out[0]
    assert isinstance(det, Detection)
    assert det.label == 'chair'
    assert det.confidence == pytest.approx(0.74)
    assert det.bbox == pytest.approx((10.0, 20.0, 30.0, 50.0))


def test_array_to_internal_skips_empty_results():
    """hypothesis 가 빈 detection 은 건너뜀 (estimator `_on_detections` 정합)."""
    arr = _mk_arr(
        _mk_det2d('chair', 0.74, 20.0, 35.0, 20.0, 30.0),
        _mk_det2d('table', 0.0, 5.0, 5.0, 2.0, 2.0, with_hyp=False),
        _mk_det2d('couch', 0.51, 40.0, 40.0, 10.0, 10.0),
    )
    out = detection2d_array_to_internal(arr)
    assert [d.label for d in out] == ['chair', 'couch']


def test_array_to_internal_empty():
    """detection 0 개 → 빈 리스트."""
    assert detection2d_array_to_internal(_mk_arr()) == []


def test_array_to_internal_score_and_label_types():
    """class_id·score 가 str·float 로 강제 변환."""
    arr = _mk_arr(_mk_det2d('cup', 1, 0.0, 0.0, 4.0, 4.0))
    det = detection2d_array_to_internal(arr)[0]
    assert isinstance(det.label, str) and det.label == 'cup'
    assert isinstance(det.confidence, float) and det.confidence == 1.0
