"""Local, case-oriented storage with no personal data in path names."""

from __future__ import annotations

import hashlib
import json
import re
import time
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


def archive_property_artifacts(paths: CasePaths) -> tuple[Path, ...]:
    """Move the current property source and derived artifacts to unique history paths."""

    token = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    moves = _property_archive_moves(paths, token=token, include_source=True)
    _replace_all_or_rollback(moves)
    return tuple(destination for _, destination in moves)


def promote_property_candidate(
    paths: CasePaths,
    candidate_source: Path,
    candidate_processed: Path,
    extraction_record: Any,
    *,
    replace_source: bool,
) -> tuple[Path, Path]:
    """Activate one fully processed property candidate and roll back failed moves."""

    root = paths.root.resolve()
    source = candidate_source.resolve()
    processed = candidate_processed.resolve()
    if not source.is_file() or not processed.is_dir():
        raise ValueError("The staged property candidate is incomplete.")
    if not source.is_relative_to(root) or not processed.is_relative_to(root):
        raise ValueError("The staged property candidate is outside the active case.")
    if not re.fullmatch(r"property-document\.(?:jpe?g|png|pdf)", source.name, re.IGNORECASE):
        raise ValueError("The staged property-document filename is invalid.")
    active_sources = list(paths.original.resolve().glob("property-document.*"))
    if not replace_source and (
        len(active_sources) != 1
        or active_sources[0].name.casefold() != source.name.casefold()
        or file_sha256(active_sources[0]) != file_sha256(source)
    ):
        raise ValueError("Only an identical active property source may be preserved.")

    token = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    active_source = paths.original.resolve() / source.name
    active_processed = paths.processed.resolve() / "property"
    active_record = root / "property_extracted.json"
    staged_record = root / f".property-extracted-{token}.json"
    write_json(staged_record, extraction_record)

    try:
        moves = _property_archive_moves(paths, token=token, include_source=replace_source)
        if replace_source:
            moves.append((source, active_source))
        moves.extend(
            [
                (processed, active_processed),
                (staged_record, active_record),
            ]
        )
        _replace_all_or_rollback(moves)
    finally:
        staged_record.unlink(missing_ok=True)
    return active_source, active_processed


def _property_archive_moves(
    paths: CasePaths,
    *,
    token: str,
    include_source: bool,
) -> list[tuple[Path, Path]]:
    root = paths.root.resolve()
    original = paths.original.resolve()
    processed = paths.processed.resolve()
    sources = list(original.glob("property-document.*"))
    if len(sources) > 1:
        raise ValueError("The case contains more than one active property document.")
    moves: list[tuple[Path, Path]] = []
    if include_source and sources:
        source = sources[0]
        moves.append(
            (
                source,
                source.with_name(f"property-document-replaced-{token}{source.suffix.lower()}"),
            )
        )
    property_record = root / "property_extracted.json"
    if property_record.is_file():
        moves.append(
            (
                property_record,
                root / f"property-extracted-replaced-{token}.json",
            )
        )
    processed_property = processed / "property"
    if processed_property.is_dir():
        moves.append(
            (
                processed_property,
                processed / f"property-replaced-{token}",
            )
        )
    return moves


def _replace_all_or_rollback(moves: list[tuple[Path, Path]]) -> None:
    sources = {source for source, _ in moves}
    destinations = [destination for _, destination in moves]
    if len(destinations) != len(set(destinations)) or any(
        destination.exists() and destination not in sources for destination in destinations
    ):
        raise ValueError("A property-document history destination already exists.")
    completed: list[tuple[Path, Path]] = []
    try:
        for source, destination in moves:
            _replace_path(source, destination)
            completed.append((source, destination))
    except Exception as error:
        rollback_errors: list[Exception] = []
        for source, destination in reversed(completed):
            try:
                _replace_path(destination, source)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise RuntimeError(
                "Property activation failed and could not be fully rolled back."
            ) from error
        raise


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def existing_case_paths(case_root: Path, expected_case_id: str) -> CasePaths:
    """Reconstruct and validate paths for the active case held in UI state."""

    root = case_root.resolve()
    if root.name != expected_case_id or not re.fullmatch(r"[A-Za-z0-9_-]+", expected_case_id):
        raise ValueError("The active case directory is invalid.")
    original = root / "original"
    processed = root / "processed"
    output = root / "output"
    if not root.is_dir() or not original.is_dir() or not processed.is_dir() or not output.is_dir():
        raise ValueError("The active case directory is incomplete.")
    return CasePaths(
        case_id=expected_case_id,
        root=root,
        original=original,
        processed=processed,
        output=output,
        extracted_json=root / "extracted.json",
        final_json=root / "final.json",
    )


def write_json(path: Path, value: Any) -> None:
    """Write JSON atomically so an interruption cannot leave a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        for attempt in range(20):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                # Windows may briefly lock the destination while another
                # process completes its own atomic replacement.
                time.sleep(0.005 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def file_sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for one stored case artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
