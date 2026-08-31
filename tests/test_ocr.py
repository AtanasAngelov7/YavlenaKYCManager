from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from ocr import PaddleOcrEngine, _address_retry_score, _normalize_result


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
