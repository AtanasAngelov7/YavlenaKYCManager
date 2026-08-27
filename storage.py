"""Local, case-oriented storage with no personal data in path names."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from models import CasePaths


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def create_case(
    original_name: str,
    content: bytes,
    cases_root: Path = Path("cases"),
    storage_stem: str | None = None,
) -> tuple[CasePaths, Path]:
    """Create a case and persist an uploaded document after basic checks."""

    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Only JPEG, PNG, and PDF documents are supported.")
    if not content:
        raise ValueError("The uploaded document is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The uploaded document exceeds the 25 MB limit.")

    case_id = f"{datetime.now():%Y-%m-%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    root = cases_root / case_id
    original = root / "original"
    processed = root / "processed"
    output = root / "output"
    for directory in (original, processed, output):
        directory.mkdir(parents=True, exist_ok=False if directory == original else True)

    paths = CasePaths(
        case_id=case_id,
        root=root,
        original=original,
        processed=processed,
        output=output,
        extracted_json=root / "extracted.json",
        final_json=root / "final.json",
    )
    source_path = save_original(paths, original_name, content, storage_stem=storage_stem)
    return paths, source_path


def save_original(
    paths: CasePaths,
    original_name: str,
    content: bytes,
    storage_stem: str | None = None,
) -> Path:
    """Save another source document inside an existing case."""

    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Only JPEG, PNG, and PDF documents are supported.")
    if not content:
        raise ValueError("The uploaded document is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The uploaded document exceeds the 25 MB limit.")

    requested_stem = storage_stem if storage_stem is not None else Path(original_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", requested_stem).strip("-")
    safe_name = f"{safe_stem or 'document'}{extension}"
    destination = paths.original / safe_name
    if destination.exists():
        raise ValueError(f"A source file named {safe_name} already exists in this case.")
    destination.write_bytes(content)
    return destination


def write_json(path: Path, value: Any) -> None:
    """Write JSON atomically so an interruption cannot leave a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)
