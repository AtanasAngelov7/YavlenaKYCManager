from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np

from ocr import (
    PaddleOcrEngine,
    _address_retry_score,
    _normalize_result,
    _ocr_import_error_message,
)


def test_shared_ocr_pipeline_serializes_concurrent_inference() -> None:
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    class DetectConcurrentPipeline:
        def predict(self, _source):
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return []

    engine = object.__new__(PaddleOcrEngine)
    engine._pipeline = DetectConcurrentPipeline()
    engine._inference_lock = threading.RLock()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda index: engine.recognize([Path(f"page-{index}.png")]),
                range(8),
            )
        )

    assert results == [[] for _ in range(8)]
    assert maximum_active == 1


def test_ocr_pipeline_initialization_is_serialized_across_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    def create_pipeline(**_options):
        nonlocal active, maximum_active
        with guard:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return object()

    monkeypatch.setattr("ocr.prepare_ocr_cache", lambda: tmp_path / "cache")
    monkeypatch.setattr("ocr.is_frozen", lambda: False)
    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=create_pipeline),
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        engines = list(pool.map(lambda _index: PaddleOcrEngine(), range(4)))

    assert len(engines) == 4
    assert maximum_active == 1


def test_frozen_ocr_forces_the_packaged_writable_cache(tmp_path: Path, monkeypatch) -> None:
    configured: dict[str, object] = {}
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "external-cache"))
    monkeypatch.setattr("ocr.prepare_ocr_cache", lambda: tmp_path / "packaged-cache")
    monkeypatch.setattr("ocr.is_frozen", lambda: True)
    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=lambda **options: configured.update(options) or object()),
    )

    PaddleOcrEngine()

    assert os.environ["PADDLE_PDX_CACHE_HOME"] == str((tmp_path / "packaged-cache").resolve())
    assert os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] == "False"
    assert configured["text_detection_model_name"] == "PP-OCRv5_mobile_det"


def test_ocr_import_error_distinguishes_missing_package_from_broken_dependency() -> None:
    missing_package = ImportError("No module named 'paddleocr'", name="paddleocr")
    broken_dependency = ImportError(
        "cannot import name 'hdrs' from partially initialized module 'aiohttp'",
        name="aiohttp",
    )

    assert "not installed in the Python environment" in _ocr_import_error_message(
        missing_package
    )
    dependency_message = _ocr_import_error_message(broken_dependency)
    assert "is installed" in dependency_message
    assert "(aiohttp)" in dependency_message


def test_normalize_current_paddleocr_result_shape() -> None:
    result = {
        "res": {
            "rec_texts": ["ИВАН", ""],
            "rec_scores": [0.97, 0.20],
            "rec_boxes": [[10, 20, 110, 45], [0, 0, 1, 1]],
        }
    }

    lines = _normalize_result(result, page_number=2)

    assert len(lines) == 1
    assert lines[0].page == 2
    assert lines[0].text == "ИВАН"
    assert lines[0].confidence == 0.97
    assert lines[0].box.left == 10


def test_recognize_top_region_uses_bounded_crop(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    assert cv2.imwrite(str(page), np.zeros((1000, 800, 3), dtype=np.uint8))

    class FakePipeline:
        def __init__(self) -> None:
            self.shape: tuple[int, ...] | None = None

        def predict(self, image: np.ndarray) -> list[dict]:
            self.shape = image.shape
            return [
                {
                    "res": {
                        "rec_texts": ["АПАРТАМЕНТ"],
                        "rec_scores": [0.98],
                        "rec_boxes": [[10, 20, 200, 50]],
                    }
                }
            ]

    engine = PaddleOcrEngine.__new__(PaddleOcrEngine)
    engine._pipeline = FakePipeline()
    engine._inference_lock = threading.RLock()

    lines, crop_bottom = engine.recognize_top_region(page, page_number=3)

    assert engine._pipeline.shape == (550, 800, 3)
    assert crop_bottom == 550
    assert lines[0].page == 3
    assert lines[0].text == "АПАРТАМЕНТ"


def test_address_retry_score_uses_generic_structure_not_sample_localities() -> None:
    structured_varna = [
        SimpleNamespace(text="общ. Варна, гр. Варна, бул. Осми приморски полк 10", confidence=0.9),
        SimpleNamespace(text="ет. 4, ап. 12", confidence=0.9),
    ]
    sample_words_without_structure = [
        SimpleNamespace(text="СОФИЯ СТОЛИЧНА ТОПЛИ ДОЛ", confidence=0.9),
    ]

    assert _address_retry_score(structured_varna) > _address_retry_score(
        sample_words_without_structure
    )
