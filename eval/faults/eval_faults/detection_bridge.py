"""Detection2DArray ↔ 내부 Detection 변환 — attribute_mismatch 채널 타입 정합.

[ADR-0029 D-A5](../../../docs/handover/decisions/0029-trial-integration-live-path.md#d-a5-attribute_mismatch-채널--detection2darray-타입-정합-신규).
OVD detector(`intent_ovd`)·estimator(live)는 fault 채널과 무관하게
``vision_msgs/Detection2DArray`` 를 주고받는다. 그러나 attribute_mismatch fault
로직(`apply_attribute_mismatch`)은 내부 `Detection`(label·xyxy bbox·confidence)
리스트 위에서 동작한다. injector 가 두 표현 사이를 변환해야 실 OVD 파이프라인에
끼어들 수 있다.

설계:
  - 메시지 → 내부(`detection2d_array_to_internal`)는 *duck-typed* 필드 접근만 →
    ``vision_msgs`` import 불요 → host venv 단위 테스트 가능(mock 입력).
  - 내부 → 메시지(`internal_to_detection2d_array`)는 메시지 객체 *생성*이 필요 →
    ``vision_msgs`` 를 함수 안에서 import(ROS 2 환경 전용).
  - bbox 좌표 표현 변환(center+size ↔ xyxy)은 순수 헬퍼로 분리 — detector_node
    `_to_detection2d` 와 동일 규약.
"""

from __future__ import annotations

from typing import List, Tuple

from eval_faults.schemas import Detection


def center_size_to_xyxy(
    cx: float, cy: float, w: float, h: float,
) -> Tuple[float, float, float, float]:
    """BoundingBox2D (center + size) → corner xyxy. detector_node 규약 역변환."""
    half_w = w * 0.5
    half_h = h * 0.5
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def xyxy_to_center_size(
    x1: float, y1: float, x2: float, y2: float,
) -> Tuple[float, float, float, float]:
    """corner xyxy → BoundingBox2D (center + size). detector_node `_to_detection2d` 규약."""
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5, x2 - x1, y2 - y1)


def detection2d_array_to_internal(arr) -> List[Detection]:  # type: ignore[no-untyped-def]
    """``vision_msgs/Detection2DArray`` → ``List[Detection]``.

    duck-typed 필드 접근만 사용 (vision_msgs import 불요). estimator `_on_detections`
    와 동일하게 hypothesis 가 빈 detection 은 건너뛴다. 첫 hypothesis(`results[0]`)
    만 사용 — OVD 는 detection 당 단일 가설을 발행(detector_node `_to_detection2d`).
    """
    out: List[Detection] = []
    for d2 in arr.detections:
        if not d2.results:
            continue
        hyp = d2.results[0].hypothesis
        bbox = d2.bbox
        x1, y1, x2, y2 = center_size_to_xyxy(
            float(bbox.center.position.x),
            float(bbox.center.position.y),
            float(bbox.size_x),
            float(bbox.size_y),
        )
        out.append(Detection(
            label=str(hyp.class_id),
            bbox=(x1, y1, x2, y2),
            confidence=float(hyp.score),
        ))
    return out


def internal_to_detection2d_array(detections: List[Detection], header):  # type: ignore[no-untyped-def]
    """``List[Detection]`` → ``vision_msgs/Detection2DArray`` (header 보존).

    detector_node `_to_detection2d_array` 패턴 미러. vision_msgs 객체 생성이
    필요해 ROS 2 환경 전용(함수 안 import).
    """
    from vision_msgs.msg import (
        BoundingBox2D,
        Detection2D,
        Detection2DArray,
        ObjectHypothesisWithPose,
    )

    arr = Detection2DArray()
    arr.header = header
    for det in detections:
        cx, cy, w, h = xyxy_to_center_size(*det.bbox)

        bbox = BoundingBox2D()
        bbox.center.position.x = float(cx)
        bbox.center.position.y = float(cy)
        bbox.center.theta = 0.0
        bbox.size_x = float(w)
        bbox.size_y = float(h)

        hyp = ObjectHypothesisWithPose()
        hyp.hypothesis.class_id = det.label
        hyp.hypothesis.score = float(det.confidence)

        d2 = Detection2D()
        d2.bbox = bbox
        d2.results.append(hyp)
        arr.detections.append(d2)
    return arr
