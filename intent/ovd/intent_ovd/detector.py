"""YOLO-World wrapper — *의도해석기* 의 ξ_ovd context 채널 공급원.

cmsm-proof §10.1 정의: ξ_ovd ∈ ξ 의 4 채널 중 하나. 입력 = 카메라 프레임 +
텍스트 어휘, 출력 = detection box + class label + confidence.

ADR-0021 1차 답: YOLO-World 단일 백본 (ultralytics 경유, Apple Silicon MPS).
Grounding DINO 는 paper-1 범위 밖.

설계 선택:
- *프레임워크-불가지 dataclass* (``Detection`` / ``DetectionResult``) 출력 —
  ROS msg / dict / Results 객체 의존성 차단. ``detector_node.py`` (후속) 가
  vision_msgs/Detection2DArray 로 직렬화.
- Lazy model load — 생성자에서 weight 안 받음. ``detect()`` 첫 호출 시 로드 또는
  ``warmup()`` 명시 호출. 테스트가 weight 없이 wrapper 로직 검증 가능.
- Mock-friendly: ``model_factory`` 파라미터로 ultralytics.YOLOWorld 대체 주입.
- Device auto: MPS 가능하면 'mps', 아니면 'cpu'. CUDA 는 paper-1 대상 호스트
  (Mac mini M4) 에 없으므로 미고려.

운용 가정 (cmsm-proof §10.5):
- (CA-2) OVD 출력 box·label·confidence 는 *경험적*으로 합리적이라고 가정.
  정확성 보장은 paper §C 실험에서 부분적으로 측정 (false-positive·miss 율).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from .vocabulary import Vocabulary


# Type alias for ultralytics.YOLOWorld-like 객체 (set_classes / predict 인터페이스).
# 테스트가 Mock 으로 대체할 수 있도록 명시. ultralytics 자체 import 는 lazy
# (의존성 없는 환경에서도 본 모듈 자체는 import 가능해야 함 — vocabulary.py 처럼).
ModelFactory = Callable[[str], object]


@dataclass(frozen=True)
class Detection:
    """단일 객체 detection.

    Args:
        xyxy: 픽셀 좌표 bbox ``(x_min, y_min, x_max, y_max)``. 부동소수.
        class_label: 어휘에 정의된 텍스트 prompt (정규화 후). 예: 'couch'.
        confidence: ``[0, 1]`` 의 신뢰도 score. ultralytics 가 산출.
        class_id: 어휘 안 index. paper §C 분석에서 prompt 추적용 (선택).
    """

    xyxy: Tuple[float, float, float, float]
    class_label: str
    confidence: float
    class_id: int = -1

    def __post_init__(self) -> None:
        if len(self.xyxy) != 4:
            raise ValueError(f"xyxy 는 4-tuple 이어야 함: {self.xyxy!r}")
        x_min, y_min, x_max, y_max = self.xyxy
        if x_max < x_min or y_max < y_min:
            raise ValueError(f"xyxy 의 max < min: {self.xyxy!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 는 [0,1]: {self.confidence}")


@dataclass(frozen=True)
class DetectionResult:
    """단일 프레임 추론 결과.

    Args:
        detections: 0 개 이상의 ``Detection``.
        image_shape: ``(H, W)`` — 정규화 좌표 계산 시 필요.
        inference_ms: model.predict() 의 wall clock 시간 (ms). 지연 예산 측정용.
        device: 'mps' / 'cpu' — 어떤 백엔드에서 돌았는지.
    """

    detections: Tuple[Detection, ...] = field(default_factory=tuple)
    image_shape: Tuple[int, int] = (0, 0)
    inference_ms: float = 0.0
    device: str = "cpu"

    def __len__(self) -> int:
        return len(self.detections)


def _select_device(prefer: str = "auto") -> str:
    """device 자동 선택.

    Args:
        prefer: 'auto' (MPS 가용하면 'mps', 아니면 'cpu'), 'mps', 'cpu' 중 하나.
            'cuda' 는 paper-1 대상 호스트에 없으므로 미지원 — die.

    Returns:
        실제 사용할 device 문자열.
    """
    if prefer not in ("auto", "mps", "cpu"):
        raise ValueError(f"prefer 는 'auto'|'mps'|'cpu' 중 하나: {prefer!r}")
    if prefer == "cpu":
        return "cpu"
    # 'auto' 또는 'mps' — torch.backends.mps 검사 (torch import 는 여기서만).
    try:
        import torch  # lazy: vocabulary.py 만 쓰는 호출자는 torch 없이도 OK.

        mps_ok = bool(torch.backends.mps.is_available())
    except Exception:
        mps_ok = False
    if prefer == "mps":
        if not mps_ok:
            raise RuntimeError("MPS 명시했으나 torch.backends.mps.is_available() = False")
        return "mps"
    # 'auto'
    return "mps" if mps_ok else "cpu"


class OVDDetector:
    """YOLO-World wrapper.

    Args:
        model_path: ultralytics 가 인식할 weight 식별자. 절대경로 또는
            'yolov8s-worldv2.pt' 같은 이름 (후자는 ultralytics 가 자동 다운로드).
            paper-1 표준 = ``models/ovd/yolov8s-worldv2.pt``.
        vocabulary: 초기 어휘. None 이면 ``set_vocabulary()`` 호출 전엔 ``detect()``
            가 에러.
        device: 'auto' (디폴트), 'mps', 'cpu'.
        conf_threshold: ultralytics predict() 의 ``conf`` 파라미터. 디폴트
            ``0.25`` — ultralytics 디폴트 값과 동일.
        model_factory: 테스트용 주입. 디폴트 None = ``ultralytics.YOLOWorld``.

    Lazy load: 생성자는 device 선택만 한다. 모델 weight 는 ``warmup()`` 또는
    첫 ``detect()`` 호출 시 로드. 이는 (a) weight 없는 환경에서 wrapper 로직
    단위 테스트 가능, (b) ROS 노드 init 단계에서 무거운 GPU 로드 차단 — node
    가 토픽 subscribe 만 일찍 잡고 첫 image 도착 시 로드.
    """

    DEFAULT_MODEL_PATH = "yolov8s-worldv2.pt"

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        vocabulary: Optional[Vocabulary] = None,
        device: str = "auto",
        conf_threshold: float = 0.25,
        model_factory: Optional[ModelFactory] = None,
    ) -> None:
        if not (0.0 <= conf_threshold <= 1.0):
            raise ValueError(f"conf_threshold 는 [0,1]: {conf_threshold}")
        self.model_path = model_path
        self.device = _select_device(device)
        self.conf_threshold = float(conf_threshold)
        self._vocabulary: Optional[Vocabulary] = vocabulary
        self._model_factory = model_factory
        self._model: Optional[object] = None  # lazy

    # ------------------------------------------------------------------ properties

    @property
    def vocabulary(self) -> Optional[Vocabulary]:
        return self._vocabulary

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------ public API

    def set_vocabulary(self, vocab: Vocabulary) -> None:
        """어휘 갱신. 모델이 이미 로드되었으면 즉시 반영, 아니면 ``warmup()`` 시점에 반영."""
        if not isinstance(vocab, Vocabulary):
            raise TypeError(f"Vocabulary 인스턴스 필요: {type(vocab)}")
        self._vocabulary = vocab
        if self._model is not None:
            # ultralytics 의 YOLOWorld 는 set_classes(list[str]) API.
            self._model.set_classes(vocab.as_list())  # type: ignore[attr-defined]

    def warmup(self) -> None:
        """모델 weight 명시적 로드 + 어휘 set. 첫 ``detect()`` 의 지연 흡수용."""
        if self._model is not None:
            return
        if self._vocabulary is None:
            raise RuntimeError(
                "warmup() 호출 전에 set_vocabulary() 필요 — 어휘 없는 YOLO-World 는 의미 없음",
            )
        factory = self._model_factory if self._model_factory is not None else _default_yolo_factory
        self._model = factory(self.model_path)
        self._model.set_classes(self._vocabulary.as_list())  # type: ignore[attr-defined]

    def detect(self, image: np.ndarray) -> DetectionResult:
        """단일 RGB 프레임에 대해 detection 수행.

        Args:
            image: ``(H, W, 3)`` uint8 numpy 배열 (RGB 또는 BGR — ultralytics 는
                BGR 가정. 호출자가 컬러 컨벤션 책임).

        Returns:
            ``DetectionResult``.

        Raises:
            RuntimeError: 어휘 미설정 상태에서 호출.
            ValueError: image shape 가 ``(H, W, 3)`` 가 아님.
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(f"image shape 는 (H, W, 3) 이어야 함: {image.shape}")
        if image.dtype not in (np.uint8, np.float32):
            raise ValueError(
                f"image dtype 는 uint8 또는 float32 이어야 함 (ultralytics 가정): {image.dtype}",
            )
        if self._vocabulary is None:
            raise RuntimeError("detect() 호출 전에 set_vocabulary() 필요")
        if self._model is None:
            self.warmup()

        t0 = time.perf_counter()
        results = self._model.predict(  # type: ignore[attr-defined]
            image,
            device=self.device,
            conf=self.conf_threshold,
            verbose=False,
        )
        inference_ms = (time.perf_counter() - t0) * 1000.0

        detections = _results_to_detections(results, self._vocabulary)
        return DetectionResult(
            detections=tuple(detections),
            image_shape=(image.shape[0], image.shape[1]),
            inference_ms=inference_ms,
            device=self.device,
        )


# ---------------------------------------------------------------------- helpers

def _default_yolo_factory(model_path: str) -> object:
    """ultralytics.YOLOWorld lazy import — wrapper 의 import-time 의존성 차단."""
    from ultralytics import YOLOWorld

    return YOLOWorld(model_path)


def _results_to_detections(
    results: Sequence[object],
    vocabulary: Vocabulary,
) -> List[Detection]:
    """ultralytics Results 리스트 → ``Detection`` 리스트.

    ultralytics 는 ``predict()`` 입력이 단일 image 여도 list of Results 반환.
    우리는 첫 결과만 사용 (단일 프레임 inference).
    """
    if not results:
        return []
    r = results[0]
    boxes = getattr(r, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    # ultralytics Results.boxes API: .xyxy / .conf / .cls — 각 N×k torch.Tensor.
    xyxy_arr = _to_numpy(boxes.xyxy)
    conf_arr = _to_numpy(boxes.conf)
    cls_arr = _to_numpy(boxes.cls).astype(int)

    vocab_list = vocabulary.as_list()
    out: List[Detection] = []
    for i in range(len(xyxy_arr)):
        cid = int(cls_arr[i])
        label = vocab_list[cid] if 0 <= cid < len(vocab_list) else f"unknown_{cid}"
        out.append(
            Detection(
                xyxy=(
                    float(xyxy_arr[i][0]),
                    float(xyxy_arr[i][1]),
                    float(xyxy_arr[i][2]),
                    float(xyxy_arr[i][3]),
                ),
                class_label=label,
                confidence=float(conf_arr[i]),
                class_id=cid,
            ),
        )
    return out


def _to_numpy(tensor_like: object) -> np.ndarray:
    """torch.Tensor 또는 numpy 배열을 numpy 로 변환 (Mock 호환)."""
    if isinstance(tensor_like, np.ndarray):
        return tensor_like
    # torch.Tensor.cpu().numpy() 패턴
    if hasattr(tensor_like, "cpu"):
        return tensor_like.cpu().numpy()  # type: ignore[no-any-return]
    if hasattr(tensor_like, "numpy"):
        return tensor_like.numpy()  # type: ignore[no-any-return]
    return np.asarray(tensor_like)
