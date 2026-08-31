"""Lazy PaddleOCR integration and normalization of provider output."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

import cv2

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

    def recognize_top_region(
        self,
        page_path: Path,
        page_number: int,
        fraction: float = 0.55,
    ) -> tuple[list[OcrLine], float]:
        """Re-run OCR on the top of a dense page, returning lines and crop boundary."""

        if not 0.2 <= fraction <= 0.8:
            raise ValueError("The OCR crop fraction must be between 0.2 and 0.8.")
        image = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
        if image is None:
            raise OcrUnavailableError(f"Could not read OCR page: {page_path.name}")
        crop_bottom = float(round(image.shape[0] * fraction))
        cropped = image[: int(crop_bottom), :]
        lines: list[OcrLine] = []
        for result in self._pipeline.predict(cropped):
            lines.extend(_normalize_result(result, page_number))
        return (
            sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left)),
            crop_bottom,
        )

    def recognize_upright_retry(self, page_path: Path, page_number: int) -> list[OcrLine]:
        """Retry a sideways image and directly recognize its small street row."""

        # The normal OCR page is intentionally capped for performance. Address
        # characters on an ID card can be only a few pixels high at that size,
        # so prefer the retained full-resolution source for this bounded retry.
        source_path = page_path.with_name(f"{page_path.stem}-source{page_path.suffix}")
        retry_path = source_path if source_path.is_file() else page_path
        image = cv2.imread(str(retry_path), cv2.IMREAD_COLOR)
        if image is None:
            raise OcrUnavailableError(f"Could not read OCR page: {page_path.name}")

        candidates: list[tuple[int, list[OcrLine], Any]] = []
        for rotation in (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_90_CLOCKWISE):
            rotated = cv2.rotate(image, rotation)
            lines: list[OcrLine] = []
            for result in self._pipeline.predict(rotated):
                lines.extend(_normalize_result(result, page_number))
            score = _address_retry_score(lines)
            candidates.append((score, lines, rotated))

        _, lines, upright = max(candidates, key=lambda candidate: candidate[0])
        street_line = next(
            (
                line
                for line in lines
                if any(token in line.text.upper() for token in ("УЛ", "БУЛ", "Ж.К", "КВ."))
            ),
            None,
        )
        if street_line is not None:
            direct_line = self._recognize_street_crop(upright, street_line, page_number)
            if direct_line is not None:
                lines.append(direct_line)
        return sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left))

    def _recognize_street_crop(
        self,
        image: Any,
        street_line: OcrLine,
        page_number: int,
    ) -> OcrLine | None:
        height, width = image.shape[:2]
        box_width = street_line.box.right - street_line.box.left
        box_height = street_line.box.bottom - street_line.box.top
        left = max(0, int(street_line.box.left - box_width * 0.25))
        top = max(0, int(street_line.box.top - box_height * 0.2))
        right = min(width, int(street_line.box.right + box_width))
        bottom = min(height, int(street_line.box.bottom + box_height * 0.7))
        if right <= left or bottom <= top:
            return None
        crop = image[top:bottom, left:right]
        crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        try:
            recognition_model = (
                self._pipeline.paddlex_pipeline._pipeline.text_rec_model
            )
            result = next(iter(recognition_model.predict(crop)))
            payload = getattr(result, "json", {})
            payload = payload() if callable(payload) else payload
            data = payload.get("res", payload) if isinstance(payload, dict) else {}
            text = str(data.get("rec_text", "")).strip()
            confidence = float(data.get("rec_score", 0.0))
        except Exception:
            return None
        if len(text) < 5 or not any(token in text.upper() for token in ("УЛ", "БУЛ", "Ж.К", "КВ.")):
            return None
        return OcrLine(
            page=page_number,
            text=text,
            confidence=max(0.0, min(1.0, confidence)),
            box=BoundingBox(left=left, top=top, right=right, bottom=bottom),
        )


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


def _address_retry_score(lines: list[OcrLine]) -> int:
    text = " ".join(line.text.upper() for line in lines)
    address_labels = {
        label
        for label, pattern in {
            "region": r"(?<!\w)ОБЛ\s*\.?",
            "municipality": r"(?<!\w)ОБЩ\s*\.?",
            "settlement": r"(?<!\w)(?:ГР|С)\s*\.?\s+",
            "street": r"(?<!\w)(?:УЛ|БУЛ)\s*\.?",
            "neighborhood": r"(?<!\w)(?:Ж\s*\.?\s*К|КВ)\s*\.?",
            "building": r"(?<!\w)(?:БЛ|ВХ|ЕТ|АП)\s*\.?",
        }.items()
        if re.search(pattern, text)
    }
    digit_score = min(sum(character.isdigit() for character in text), 12)
    confidence_score = round(sum(line.confidence for line in lines if line.text.strip()))
    return 3 * len(address_labels) + digit_score + confidence_score


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
