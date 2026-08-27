"""Convert supported documents into OCR-ready page images."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError


PDF_RENDER_DPI = 300
MAX_PDF_PAGES = 10


class DocumentProcessingError(ValueError):
    pass


def prepare_document(source: Path, output_directory: Path) -> list[Path]:
    """Render and enhance *source*, returning one PNG path per page."""

    output_directory.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".pdf":
        source_pages = _render_pdf(source, output_directory)
    else:
        source_pages = [_normalize_image(source, output_directory / "page-1-source.png")]

    processed_pages: list[Path] = []
    for page_number, source_page in enumerate(source_pages, start=1):
        destination = output_directory / f"page-{page_number}.png"
        _enhance_for_ocr(source_page, destination)
        processed_pages.append(destination)
    return processed_pages


def _render_pdf(source: Path, output_directory: Path) -> list[Path]:
    try:
        document = pymupdf.open(source)
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise DocumentProcessingError("The PDF could not be opened.") from error

    with document:
        if document.page_count == 0:
            raise DocumentProcessingError("The PDF has no pages.")
        if document.page_count > MAX_PDF_PAGES:
            raise DocumentProcessingError(f"PDFs are limited to {MAX_PDF_PAGES} pages.")

        zoom = PDF_RENDER_DPI / 72
        matrix = pymupdf.Matrix(zoom, zoom)
        page_paths: list[Path] = []
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            page_path = output_directory / f"page-{index + 1}-source.png"
            pixmap.save(page_path)
            page_paths.append(page_path)
        return page_paths


def _normalize_image(source: Path, destination: Path) -> Path:
    try:
        with Image.open(source) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.save(destination, format="PNG")
    except (UnidentifiedImageError, OSError) as error:
        raise DocumentProcessingError("The uploaded image could not be read.") from error
    return destination


def _enhance_for_ocr(source: Path, destination: Path) -> None:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise DocumentProcessingError(f"Could not read rendered page: {source.name}")

    # Preserve enough resolution for small identity-card text without creating
    # very large intermediate files.
    height, width = image.shape[:2]
    longest_side = max(height, width)
    if longest_side < 1600:
        scale = 1600 / longest_side
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif longest_side > 2400:
        # Large phone photos make CPU OCR disproportionately slow without
        # improving identity-card text enough to justify the cost.
        scale = 2400 / longest_side
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    luminance, channel_a, channel_b = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2LAB))
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_luminance = clahe.apply(luminance)
    enhanced = cv2.cvtColor(
        cv2.merge((enhanced_luminance, channel_a, channel_b)),
        cv2.COLOR_LAB2BGR,
    )

    # A light unsharp mask improves small characters while retaining document
    # color and security patterns for operator review.
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(enhanced, 1.35, blurred, -0.35, 0)
    if not cv2.imwrite(str(destination), np.asarray(sharpened)):
        raise DocumentProcessingError(f"Could not save processed page: {destination.name}")
