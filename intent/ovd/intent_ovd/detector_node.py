"""ROS 2 노드 — OVD detector wrapper 를 image 토픽에 wire.

데이터 흐름:
    /camera/image_raw (sensor_msgs/Image, SENSOR_DATA QoS)
        → cv_bridge → numpy.ndarray (BGR8)
        → OVDDetector.detect() (MPS / cpu)
        → vision_msgs/Detection2DArray (default reliable QoS)
        → /intent/ovd/detections

ROS params (모두 launch 또는 ros2 run 에서 override 가능):
    model_path: str — ultralytics weight 식별자. 절대경로 또는 'yolov8s-worldv2.pt'
        (후자는 cwd 또는 ultralytics 캐시에서 찾음). paper-1 표준 = 절대경로로
        ``$REPO_ROOT/models/ovd/yolov8s-worldv2.pt`` 명시 (launch 파일이 처리).
        *신뢰 boundary*: launch 파일이 controls trust — 임의 weight 다운로드는 launch
        가 명시한 경로일 때만 발생. ROS topic 으로 model_path 받지 않음.
    vocabulary: string[] — 초기 어휘 prompt 들. 빈 list 면 die. *정적* 잠금 —
        runtime 변경은 paper-1 범위 밖 (명료화 루프 B4 는 후속).
    device: 'auto' | 'mps' | 'cpu' — 디폴트 'auto'.
    conf_threshold: double, default 0.25.
    input_image_topic: str, default '/camera/image_raw'.
    output_detection_topic: str, default '/intent/ovd/detections'.
    throttle_hz: double, default 0.0 — 0 이면 매 프레임 추론, > 0 이면 최소 간격
        ``1/throttle_hz`` 초 보다 짧으면 frame skip. 시나리오가 결정.

QoS:
    Sub  /camera/image_raw         — rclpy.qos.qos_profile_sensor_data (best_effort, depth 5)
    Pub  /intent/ovd/detections    — default (reliable, depth 10)

cmsm-proof §10.1 ξ_ovd context 채널 공급원. ROADMAP §3 B1.
"""

from __future__ import annotations

import time
from typing import List, Optional

import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from vision_msgs.msg import (
    BoundingBox2D,
    Detection2D,
    Detection2DArray,
    ObjectHypothesisWithPose,
)

from .detector import Detection, DetectionResult, OVDDetector
from .vocabulary import Vocabulary


class OVDDetectorNode(Node):
    """OVD detector 의 ROS 2 wrapping. cmsm-proof §10.1 ξ_ovd 공급원."""

    def __init__(self) -> None:
        super().__init__("ovd_detector")

        # --- params ---
        self.declare_parameter("model_path", OVDDetector.DEFAULT_MODEL_PATH)
        self.declare_parameter("vocabulary", [""])  # 빈 string list 디폴트 → 명시 강제
        self.declare_parameter("device", "auto")
        self.declare_parameter("conf_threshold", 0.25)
        self.declare_parameter("input_image_topic", "/camera/image_raw")
        self.declare_parameter("output_detection_topic", "/intent/ovd/detections")
        self.declare_parameter("throttle_hz", 0.0)

        model_path = str(self.get_parameter("model_path").value)
        vocab_raw = list(self.get_parameter("vocabulary").value or [])
        device = str(self.get_parameter("device").value)
        conf_threshold = float(self.get_parameter("conf_threshold").value)
        input_topic = str(self.get_parameter("input_image_topic").value)
        output_topic = str(self.get_parameter("output_detection_topic").value)
        throttle_hz = float(self.get_parameter("throttle_hz").value)

        # --- vocabulary 정규화 ---
        # ROS param 의 string[] 가 [""] (싱글 빈 문자열) 디폴트로 오는 경우 거부.
        cleaned = [v for v in vocab_raw if isinstance(v, str) and v.strip()]
        if not cleaned:
            raise RuntimeError(
                "ROS param 'vocabulary' 가 비었거나 빈 문자열만 — launch 파일에서 "
                "최소 1 개의 prompt 명시 필요.",
            )
        vocabulary = Vocabulary.from_strings(cleaned)
        self.get_logger().info(
            f"OVD vocabulary ({len(vocabulary)}): {vocabulary.as_list()}",
        )

        # --- detector + bridge ---
        self._detector = OVDDetector(
            model_path=model_path,
            vocabulary=vocabulary,
            device=device,
            conf_threshold=conf_threshold,
        )
        self._bridge = CvBridge()
        self._throttle_period = 1.0 / throttle_hz if throttle_hz > 0.0 else 0.0
        self._last_inference_ts: float = 0.0
        self._n_frames_in: int = 0
        self._n_frames_out: int = 0

        # --- pub / sub ---
        self._publisher = self.create_publisher(Detection2DArray, output_topic, 10)
        self._subscription = self.create_subscription(
            Image,
            input_topic,
            self._on_image,
            qos_profile_sensor_data,
        )

        self.get_logger().info(
            f"OVDDetectorNode ready — model={model_path}, device={self._detector.device}, "
            f"conf={conf_threshold}, throttle_hz={throttle_hz}, "
            f"in={input_topic}, out={output_topic}",
        )

    # ------------------------------------------------------------------ callback

    def _on_image(self, msg: Image) -> None:
        self._n_frames_in += 1

        # Throttle: 마지막 inference 와 충분히 떨어졌을 때만 진행.
        if self._throttle_period > 0.0:
            now = time.monotonic()
            if (now - self._last_inference_ts) < self._throttle_period:
                return
            self._last_inference_ts = now

        # cv_bridge → numpy BGR8.
        try:
            image = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as e:
            self.get_logger().warn(f"cv_bridge 실패 ({type(e).__name__}): {e}")
            return

        try:
            result = self._detector.detect(image)
        except Exception as e:
            self.get_logger().error(f"OVD 추론 실패 ({type(e).__name__}): {e}")
            return

        self._n_frames_out += 1
        msg_out = _to_detection2d_array(result, msg.header)
        self._publisher.publish(msg_out)

        if self._n_frames_in % 30 == 0:
            self.get_logger().debug(
                f"frames in={self._n_frames_in} out={self._n_frames_out} "
                f"last_inference_ms={result.inference_ms:.1f} device={result.device}",
            )


# ---------------------------------------------------------------------- helpers

def _to_detection2d_array(result: DetectionResult, header) -> Detection2DArray:  # type: ignore[no-untyped-def]
    """``DetectionResult`` → ``vision_msgs/Detection2DArray``.

    헤더는 입력 Image 의 ``header`` 그대로 (frame_id + stamp 보존 — 후속 노드가
    image 와 동기화 가능).
    """
    arr = Detection2DArray()
    arr.header = header
    for det in result.detections:
        arr.detections.append(_to_detection2d(det))
    return arr


def _to_detection2d(det: Detection) -> Detection2D:
    """``Detection`` (xyxy) → ``vision_msgs/Detection2D`` (center + size)."""
    x_min, y_min, x_max, y_max = det.xyxy
    cx = (x_min + x_max) * 0.5
    cy = (y_min + y_max) * 0.5
    w = x_max - x_min
    h = y_max - y_min

    bbox = BoundingBox2D()
    bbox.center.position.x = float(cx)
    bbox.center.position.y = float(cy)
    bbox.center.theta = 0.0
    bbox.size_x = float(w)
    bbox.size_y = float(h)

    hyp = ObjectHypothesisWithPose()
    hyp.hypothesis.class_id = det.class_label
    hyp.hypothesis.score = float(det.confidence)

    d2 = Detection2D()
    d2.bbox = bbox
    d2.results.append(hyp)
    return d2


# ---------------------------------------------------------------------- main

def main(args: Optional[List[str]] = None) -> None:
    rclpy.init(args=args)
    node = OVDDetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
