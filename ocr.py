"""Lazy PaddleOCR integration and normalization of provider output."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from models import BoundingBox, OcrLine


class OcrUnavailableError(RuntimeError):
    pass


class PaddleOcrEngine:
    """Small adapter around PaddleOCR's general OCR pipeline."""

    def __init__(self) -> None:
        # Keep downloaded models and PaddleX temporary files inside this local
        # project instead of scattering them through the user's profile.
        cache_directory = Path(".local") / "paddlex"
        cache_directory.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_directory.resolve()))
        # PaddlePaddle 3.3's Windows CPU oneDNN path cannot execute the array
        # attribute used by the selected detection model. The regular CPU
        # inference path is slightly slower but reliable for this local app.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")

        try:
            from paddleocr import PaddleOCR
        except ImportError as error:
            raise OcrUnavailableError(
                "PaddleOCR is not installed. Install the project requirements first."
            ) from error

        # Mobile detection is a better CPU/local default than the much slower
        # server detector. The Cyrillic model includes Bulgarian and English.
        # Model files are downloaded on first use and then used locally.
        self._pipeline = PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="cyrillic_PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_limit_side_len=2400,
            text_det_limit_type="max",
            device="cpu",
        )

    def recognize(self, pages: Iterable[Path]) -> list[OcrLine]:
        lines: list[OcrLine] = []
        for page_number, page_path in enumerate(pages, start=1):
            results = self._pipeline.predict(str(page_path))
            for result in results:
                lines.extend(_normalize_result(result, page_number))
        return sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left))


def _normalize_result(result: Any, page_number: int) -> list[OcrLine]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        raise OcrUnavailableError("PaddleOCR returned an unexpected result type.")

    data = payload.get("res", payload)
    texts = _as_list(data.get("rec_texts", []))
    scores = _as_list(data.get("rec_scores", []))
    boxes = _as_list(data.get("rec_boxes", []))
    polygons = _as_list(data.get("rec_polys", []))

    normalized: list[OcrLine] = []
    for index, raw_text in enumerate(texts):
        text = str(raw_text).strip()
        if not text:
            continue
        score = float(scores[index]) if index < len(scores) else 0.0
        raw_box = boxes[index] if index < len(boxes) else None
        if raw_box is None and index < len(polygons):
            raw_box = _polygon_to_box(polygons[index])
        if raw_box is None or len(raw_box) < 4:
            continue

        normalized.append(
            OcrLine(
                page=page_number,
                text=text,
                confidence=max(0.0, min(1.0, score)),
                box=BoundingBox(
                    left=float(raw_box[0]),
                    top=float(raw_box[1]),
                    right=float(raw_box[2]),
                    bottom=float(raw_box[3]),
                ),
            )
        )
    return normalized


def _polygon_to_box(polygon: Any) -> list[float] | None:
    points = _as_list(polygon)
    if not points:
        return None
    x_values = [float(_as_list(point)[0]) for point in points]
    y_values = [float(_as_list(point)[1]) for point in points]
    return [min(x_values), min(y_values), max(x_values), max(y_values)]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)
