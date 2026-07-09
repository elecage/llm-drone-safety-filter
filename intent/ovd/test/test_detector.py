"""OVDDetector 단위 테스트 (Mock 기반).

실제 ultralytics YOLOWorld 호출은 ``@pytest.mark.requires_weights`` 로 분리.
디폴트 pytest 실행은 mock 만 사용 — weight 다운로드 없이 wrapper 로직 자체를
검증.
"""

from __future__ import annotations

from typing import List, Optional
from unittest.mock import MagicMock

import numpy as np
import pytest

from intent_ovd.detector import (
    Detection,
    DetectionResult,
    OVDDetector,
    _results_to_detections,
    _select_device,
)
from intent_ovd.vocabulary import Vocabulary


# ---------------------------------------------------------------------- fixtures

@pytest.fixture
def vocab() -> Vocabulary:
    return Vocabulary.from_strings(["couch", "table", "chair"])


@pytest.fixture
def dummy_image() -> np.ndarray:
    """640×480 BGR uint8 zeros."""
    return np.zeros((480, 640, 3), dtype=np.uint8)


class FakeBoxes:
    """ultralytics Results.boxes 모사 — .xyxy / .conf / .cls 어트리뷰트만."""

    def __init__(
        self,
        xyxy: np.ndarray,
        conf: np.ndarray,
        cls: np.ndarray,
    ) -> None:
        self.xyxy = xyxy
        self.conf = conf
        self.cls = cls

    def __len__(self) -> int:
        return len(self.xyxy)


class FakeResult:
    """ultralytics Results 모사."""

    def __init__(self, boxes: Optional[FakeBoxes]) -> None:
        self.boxes = boxes


def make_fake_model(results_to_return: List[FakeResult]) -> MagicMock:
    """OVDDetector 의 model_factory 에 주입 가능한 model mock 생성기.

    set_classes / predict 두 메서드를 흉내냄.
    """
    model = MagicMock()
    model.predict.return_value = results_to_return
    return model


# ---------------------------------------------------------------------- _select_device

class TestSelectDevice:
    def test_cpu_explicit(self) -> None:
        assert _select_device("cpu") == "cpu"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _select_device("cuda")

    def test_auto_returns_one_of_known(self) -> None:
        result = _select_device("auto")
        assert result in ("mps", "cpu")

    def test_mps_explicit_when_unavailable_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``prefer='mps'`` 인데 torch.backends.mps.is_available() = False 면 die."""
        import torch

        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        with pytest.raises(RuntimeError, match="MPS"):
            _select_device("mps")

    def test_auto_falls_back_to_cpu_when_mps_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``prefer='auto'`` 인데 MPS 가용 안 하면 'cpu'."""
        import torch

        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        assert _select_device("auto") == "cpu"


# ---------------------------------------------------------------------- OVDDetector init / props

class TestInit:
    def test_default_lazy_load(self, vocab: Vocabulary) -> None:
        det = OVDDetector(vocabulary=vocab, device="cpu")
        assert det.is_loaded is False
        assert det.vocabulary is vocab
        assert det.device == "cpu"
        assert det.conf_threshold == 0.25

    def test_no_vocabulary_ok(self) -> None:
        det = OVDDetector(device="cpu")
        assert det.vocabulary is None
        assert det.is_loaded is False

    def test_invalid_conf_threshold(self) -> None:
        with pytest.raises(ValueError):
            OVDDetector(conf_threshold=1.5, device="cpu")


# ---------------------------------------------------------------------- set_vocabulary

class TestSetVocabulary:
    def test_sets_attribute(self, vocab: Vocabulary) -> None:
        det = OVDDetector(device="cpu")
        det.set_vocabulary(vocab)
        assert det.vocabulary is vocab

    def test_type_check(self) -> None:
        det = OVDDetector(device="cpu")
        with pytest.raises(TypeError):
            det.set_vocabulary("couch")  # type: ignore[arg-type]

    def test_propagates_to_loaded_model(self, vocab: Vocabulary) -> None:
        fake_model = make_fake_model([FakeResult(None)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            model_factory=lambda _: fake_model,
        )
        det.warmup()
        # warmup 직후 set_classes 가 어휘 list 로 호출됨.
        fake_model.set_classes.assert_called_with(vocab.as_list())
        # 새 어휘로 갱신 → set_classes 가 다시 호출됨.
        new_vocab = Vocabulary.from_strings(["lamp"])
        det.set_vocabulary(new_vocab)
        assert fake_model.set_classes.call_args.args[0] == ["lamp"]


# ---------------------------------------------------------------------- warmup

class TestWarmup:
    def test_requires_vocabulary(self) -> None:
        det = OVDDetector(device="cpu")
        with pytest.raises(RuntimeError, match="set_vocabulary"):
            det.warmup()

    def test_loads_model_and_sets_classes(self, vocab: Vocabulary) -> None:
        fake_model = make_fake_model([FakeResult(None)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            model_factory=lambda _: fake_model,
        )
        det.warmup()
        assert det.is_loaded is True
        fake_model.set_classes.assert_called_once_with(["couch", "table", "chair"])

    def test_idempotent(self, vocab: Vocabulary) -> None:
        fake_model = make_fake_model([FakeResult(None)])
        factory_calls: List[str] = []

        def factory(path: str) -> MagicMock:
            factory_calls.append(path)
            return fake_model

        det = OVDDetector(vocabulary=vocab, device="cpu", model_factory=factory)
        det.warmup()
        det.warmup()  # 두 번째는 무동작.
        assert len(factory_calls) == 1


# ---------------------------------------------------------------------- detect

class TestDetect:
    def test_requires_vocabulary(self, dummy_image: np.ndarray) -> None:
        det = OVDDetector(device="cpu")
        with pytest.raises(RuntimeError, match="set_vocabulary"):
            det.detect(dummy_image)

    def test_invalid_shape(self, vocab: Vocabulary) -> None:
        det = OVDDetector(vocabulary=vocab, device="cpu")
        with pytest.raises(ValueError, match="image shape"):
            det.detect(np.zeros((480, 640), dtype=np.uint8))

    def test_invalid_dtype(self, vocab: Vocabulary) -> None:
        """ultralytics 가 가정하는 uint8 / float32 외 dtype 거부."""
        det = OVDDetector(vocabulary=vocab, device="cpu")
        with pytest.raises(ValueError, match="dtype"):
            det.detect(np.zeros((480, 640, 3), dtype=np.int32))

    def test_float32_dtype_ok(self, vocab: Vocabulary) -> None:
        fake_model = make_fake_model([FakeResult(None)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            model_factory=lambda _: fake_model,
        )
        result = det.detect(np.zeros((480, 640, 3), dtype=np.float32))
        assert len(result) == 0

    def test_empty_result(self, vocab: Vocabulary, dummy_image: np.ndarray) -> None:
        fake_model = make_fake_model([FakeResult(None)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            model_factory=lambda _: fake_model,
        )
        result = det.detect(dummy_image)
        assert isinstance(result, DetectionResult)
        assert len(result) == 0
        assert result.image_shape == (480, 640)
        assert result.device == "cpu"
        assert result.inference_ms >= 0.0

    def test_populated_result(self, vocab: Vocabulary, dummy_image: np.ndarray) -> None:
        fake_boxes = FakeBoxes(
            xyxy=np.array([[10.0, 20.0, 100.0, 150.0], [200.0, 50.0, 300.0, 200.0]]),
            conf=np.array([0.9, 0.7]),
            cls=np.array([0, 1]),
        )
        fake_model = make_fake_model([FakeResult(fake_boxes)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            model_factory=lambda _: fake_model,
        )
        result = det.detect(dummy_image)
        assert len(result) == 2
        d0, d1 = result.detections
        assert d0.class_label == "couch"
        assert d0.class_id == 0
        assert d0.confidence == pytest.approx(0.9)
        assert d0.xyxy == (10.0, 20.0, 100.0, 150.0)
        assert d1.class_label == "table"
        assert d1.confidence == pytest.approx(0.7)

    def test_predict_kwargs(self, vocab: Vocabulary, dummy_image: np.ndarray) -> None:
        """predict() 가 ``device`` / ``conf`` / ``verbose=False`` 로 호출되는지."""
        fake_model = make_fake_model([FakeResult(None)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            conf_threshold=0.4,
            model_factory=lambda _: fake_model,
        )
        det.detect(dummy_image)
        kwargs = fake_model.predict.call_args.kwargs
        assert kwargs["device"] == "cpu"
        assert kwargs["conf"] == pytest.approx(0.4)
        assert kwargs["verbose"] is False

    def test_lazy_load_triggered_by_detect(
        self,
        vocab: Vocabulary,
        dummy_image: np.ndarray,
    ) -> None:
        fake_model = make_fake_model([FakeResult(None)])
        det = OVDDetector(
            vocabulary=vocab,
            device="cpu",
            model_factory=lambda _: fake_model,
        )
        assert det.is_loaded is False
        det.detect(dummy_image)
        assert det.is_loaded is True


# ---------------------------------------------------------------------- Detection / DetectionResult 자체

class TestDetectionDataclass:
    def test_valid(self) -> None:
        d = Detection(xyxy=(0.0, 0.0, 10.0, 10.0), class_label="x", confidence=0.5)
        assert d.class_id == -1  # default

    def test_xyxy_wrong_length(self) -> None:
        with pytest.raises(ValueError):
            Detection(xyxy=(0.0, 0.0, 10.0), class_label="x", confidence=0.5)  # type: ignore[arg-type]

    def test_xyxy_max_less_than_min(self) -> None:
        with pytest.raises(ValueError, match="max < min"):
            Detection(xyxy=(10.0, 0.0, 5.0, 5.0), class_label="x", confidence=0.5)

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            Detection(xyxy=(0.0, 0.0, 1.0, 1.0), class_label="x", confidence=1.5)


# ---------------------------------------------------------------------- _results_to_detections

class TestResultsToDetections:
    def test_empty_list(self, vocab: Vocabulary) -> None:
        assert _results_to_detections([], vocab) == []

    def test_no_boxes(self, vocab: Vocabulary) -> None:
        assert _results_to_detections([FakeResult(None)], vocab) == []

    def test_unknown_class_id_falls_back(self, vocab: Vocabulary) -> None:
        """class_id 가 어휘 범위 밖이면 'unknown_{cid}' label."""
        boxes = FakeBoxes(
            xyxy=np.array([[0.0, 0.0, 10.0, 10.0]]),
            conf=np.array([0.5]),
            cls=np.array([99]),
        )
        dets = _results_to_detections([FakeResult(boxes)], vocab)
        assert dets[0].class_label == "unknown_99"

    def test_uses_only_first_result(self, vocab: Vocabulary) -> None:
        """ultralytics 는 multi-image 호출 시 multiple Results; 우리는 첫 것만."""
        b0 = FakeBoxes(
            xyxy=np.array([[0.0, 0.0, 10.0, 10.0]]),
            conf=np.array([0.5]),
            cls=np.array([0]),
        )
        b1 = FakeBoxes(
            xyxy=np.array([[100.0, 100.0, 200.0, 200.0]]),
            conf=np.array([0.8]),
            cls=np.array([1]),
        )
        dets = _results_to_detections([FakeResult(b0), FakeResult(b1)], vocab)
        assert len(dets) == 1
        assert dets[0].class_label == "couch"


# ---------------------------------------------------------------------- 통합 (requires_weights)

@pytest.mark.requires_weights
class TestRealInference:
    """실제 YOLOWorld 가중치로 추론. 디폴트 SKIP — OVD_RUN_INTEGRATION=1 일 때만.

    Weight 는 *canonical 경로* (``$REPO_ROOT/models/ovd/yolov8s-worldv2.pt``) 에
    존재해야 함 — ``OVD_FETCH_WEIGHTS=1 scripts/install_ovd.sh`` 로 받아둠.
    ultralytics 가 cwd 에 떨구지 못하도록 명시 경로 전달.
    """

    @staticmethod
    def _weight_path() -> str:
        # intent/ovd/test/test_detector.py → 3 단계 위가 repo root.
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        return str(repo_root / "models" / "ovd" / "yolov8s-worldv2.pt")

    def test_can_detect_on_blank_image(self, vocab: Vocabulary) -> None:
        """0-tensor image 에서도 추론이 *동작* 은 함 (detection 0 개여도 OK)."""
        from pathlib import Path

        weight = self._weight_path()
        if not Path(weight).is_file():
            pytest.skip(
                f"Weight 미존재: {weight}. "
                f"`OVD_FETCH_WEIGHTS=1 ./scripts/install_ovd.sh` 로 받기.",
            )
        det = OVDDetector(model_path=weight, vocabulary=vocab, device="auto")
        result = det.detect(np.zeros((480, 640, 3), dtype=np.uint8))
        assert isinstance(result, DetectionResult)
        assert result.device in ("mps", "cpu")
        assert result.inference_ms > 0.0
