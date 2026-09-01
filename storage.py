"""Local, case-oriented storage with no personal data in path names."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from models import ApprovedIdentitySnapshot, CasePaths, ExtractionResult
from runtime_paths import CASES_ROOT


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CASE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
CASE_RESIDUE_PATTERN = re.compile(r"^\.(?:creating|deleting)-(?P<case_id>[A-Za-z0-9_-]+)$")
PROPERTY_CANDIDATE_PATTERN = re.compile(r"^\.property-candidate-[A-Za-z0-9_-]+$")


class CaseBusyError(ValueError):
    """Raised when another local UI session is mutating the same case."""


class CaseCleanupError(OSError):
    """Raised when private case artifacts could not be fully removed."""


@dataclass(frozen=True)
class ValidatedIdentitySnapshot:
    """One approved identity and the exact extraction to which it is bound."""

    snapshot: ApprovedIdentitySnapshot
    extraction: ExtractionResult
    snapshot_sha256: str
    extraction_sha256: str


def create_case(
    original_name: str,
    content: bytes,
    cases_root: Path | None = None,
    storage_stem: str | None = None,
) -> tuple[CasePaths, Path]:
    """Create a case and persist an uploaded document after basic checks."""

    effective_cases_root = (cases_root or CASES_ROOT).resolve()
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Only JPEG, PNG, and PDF documents are supported.")
    if not content:
        raise ValueError("The uploaded document is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The uploaded document exceeds the 25 MB limit.")

    case_id = f"{datetime.now():%Y-%m-%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    effective_cases_root.mkdir(parents=True, exist_ok=True)
    root = effective_cases_root / case_id
    staging_root = effective_cases_root / f".creating-{case_id}"
    if root.exists() or staging_root.exists():
        raise ValueError("A case with the generated identifier already exists.")

    with _case_id_lock(effective_cases_root, case_id):
        staging_paths = _case_paths(staging_root, case_id)
        try:
            for directory in (
                staging_paths.original,
                staging_paths.processed,
                staging_paths.output,
            ):
                directory.mkdir(parents=True, exist_ok=False)
            staged_source = save_original(
                staging_paths,
                original_name,
                content,
                storage_stem=storage_stem,
            )
            # Windows scanners can briefly hold a newly written upload directory.
            # Use the same bounded atomic-rename retry as other case promotions.
            _replace_path(staging_root, root)
        except Exception as error:
            try:
                if staging_root.exists():
                    shutil.rmtree(staging_root)
            except OSError as cleanup_error:
                raise CaseCleanupError(
                    "Case creation failed and temporary personal files could not be fully removed."
                ) from cleanup_error
            raise error

    paths = _case_paths(root, case_id)
    return paths, paths.original / staged_source.name


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
    candidate_root = source.parent.parent
    if (
        source.parent.name != "original"
        or processed.name != "processed-property"
        or processed.parent != candidate_root
        or candidate_root.parent != root
        or PROPERTY_CANDIDATE_PATTERN.fullmatch(candidate_root.name) is None
    ):
        raise ValueError("The staged property candidate has an invalid directory structure.")
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
    staged_record = candidate_root / "property_extracted.json"
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
    for attempt in range(20):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005 * (attempt + 1))


def existing_case_paths(case_root: Path, expected_case_id: str) -> CasePaths:
    """Reconstruct and validate paths for the active case held in UI state."""

    root = case_root.resolve()
    if root.name != expected_case_id or CASE_ID_PATTERN.fullmatch(expected_case_id) is None:
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


def read_validated_identity_snapshot(
    final_json: Path,
    *,
    expected_case_id: str = "",
    expected_snapshot_sha256: str = "",
) -> ValidatedIdentitySnapshot:
    """Validate an approved identity against its case and exact OCR extraction."""

    path = final_json.resolve()
    case_id = expected_case_id or path.parent.name
    if (
        path.name != "final.json"
        or CASE_ID_PATTERN.fullmatch(case_id) is None
        or path.parent.name != case_id
    ):
        raise ValueError("The reviewed identity path or case identifier is invalid.")
    try:
        snapshot_payload = path.read_bytes()
    except OSError as error:
        raise ValueError("The reviewed identity snapshot is missing or unreadable.") from error
    snapshot_sha256 = hashlib.sha256(snapshot_payload).hexdigest()
    if expected_snapshot_sha256 and snapshot_sha256 != expected_snapshot_sha256:
        raise ValueError("The reviewed identity snapshot changed after it was selected.")
    try:
        snapshot = ApprovedIdentitySnapshot.model_validate_json(snapshot_payload)
    except ValueError as error:
        raise ValueError("The reviewed identity snapshot is invalid.") from error
    if snapshot.case_id != case_id:
        raise ValueError("The reviewed identity snapshot belongs to a different case.")

    extraction_path = path.parent / "extracted.json"
    try:
        extraction_payload = extraction_path.read_bytes()
        extraction = ExtractionResult.model_validate_json(extraction_payload)
    except (OSError, ValueError) as error:
        raise ValueError("The case OCR extraction is missing or invalid.") from error
    extraction_sha256 = hashlib.sha256(extraction_payload).hexdigest()
    if extraction.case_id != case_id or extraction_sha256 != snapshot.extracted_sha256:
        raise ValueError(
            "The OCR extraction changed after identity review. Review and save the identity again."
        )
    return ValidatedIdentitySnapshot(
        snapshot=snapshot,
        extraction=extraction,
        snapshot_sha256=snapshot_sha256,
        extraction_sha256=extraction_sha256,
    )


def list_local_cases(cases_root: Path | None = None) -> tuple[CasePaths, ...]:
    """Return complete case directories, newest first, without reading personal data."""

    effective_root = (cases_root or CASES_ROOT).resolve()
    try:
        candidates = tuple(effective_root.iterdir())
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise ValueError("The local cases directory cannot be read.") from error

    cases: list[CasePaths] = []
    for candidate in candidates:
        try:
            if not candidate.is_dir() or CASE_ID_PATTERN.fullmatch(candidate.name) is None:
                continue
            if candidate.resolve().parent != effective_root:
                continue
            cases.append(existing_case_paths(candidate, candidate.name))
        except (OSError, ValueError):
            continue

    def modified_time(paths: CasePaths) -> int:
        try:
            return paths.root.stat().st_mtime_ns
        except OSError:
            return 0

    return tuple(
        sorted(
            cases,
            key=lambda paths: (modified_time(paths), paths.case_id),
            reverse=True,
        )
    )


def delete_local_case(
    paths: CasePaths,
    cases_root: Path | None = None,
    blocked: Callable[[], bool] | None = None,
) -> None:
    """Atomically retire one validated case, then remove its private files."""

    effective_root = (cases_root or CASES_ROOT).resolve()
    case_root = paths.root.resolve()
    if (
        case_root.parent != effective_root
        or case_root.name != paths.case_id
        or CASE_ID_PATTERN.fullmatch(paths.case_id) is None
    ):
        raise ValueError("The case selected for deletion is outside the local cases directory.")
    if not case_root.is_dir():
        return
    residue = effective_root / f".deleting-{paths.case_id}"
    with case_mutation_lock(paths, effective_root):
        if blocked is not None and blocked():
            raise CaseBusyError("Close the active RMS browser before deleting this case.")
        if residue.exists():
            raise CaseCleanupError(
                "A previous cleanup for this case is still pending; retry private-file cleanup."
            )
        case_root.replace(residue)
        try:
            shutil.rmtree(residue)
        except OSError as error:
            raise CaseCleanupError(
                "The case was retired, but some private files remain pending cleanup."
            ) from error


@contextmanager
def case_mutation_lock(
    paths: CasePaths,
    cases_root: Path | None = None,
) -> Iterator[None]:
    """Serialize mutations for one direct-child case across sessions and processes."""

    effective_root = (cases_root or CASES_ROOT).resolve()
    case_root = paths.root.resolve()
    if (
        case_root.parent != effective_root
        or case_root.name != paths.case_id
        or CASE_ID_PATTERN.fullmatch(paths.case_id) is None
    ):
        raise ValueError("The case selected for mutation is outside the local cases directory.")
    if not case_root.is_dir():
        raise ValueError("The active case directory is missing.")
    with _case_id_lock(effective_root, paths.case_id):
        # The case can be deleted after the optimistic check above but before
        # this process obtains the lock. Re-resolve and revalidate while the
        # lock is held so a stale tab cannot mutate or recreate a retired case.
        locked_case_root = paths.root.resolve()
        if (
            locked_case_root != case_root
            or locked_case_root.parent != effective_root
            or locked_case_root.name != paths.case_id
            or not locked_case_root.is_dir()
        ):
            raise ValueError("The active case directory changed or was removed before the operation began.")
        yield


def list_case_cleanup_residue(cases_root: Path | None = None) -> tuple[Path, ...]:
    """Return interrupted lifecycle and property-staging directories without reading their data."""

    effective_root = (cases_root or CASES_ROOT).resolve()
    try:
        candidates = tuple(effective_root.iterdir())
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise ValueError("The local cases directory cannot be read.") from error
    residue: list[Path] = []
    for candidate in candidates:
        try:
            if (
                CASE_RESIDUE_PATTERN.fullmatch(candidate.name) is not None
                and candidate.is_dir()
                and not candidate.is_symlink()
                and candidate.resolve().parent == effective_root
            ):
                residue.append(candidate)
                continue
            if (
                CASE_ID_PATTERN.fullmatch(candidate.name) is not None
                and candidate.is_dir()
                and not candidate.is_symlink()
                and candidate.resolve().parent == effective_root
            ):
                residue.extend(
                    child
                    for child in candidate.iterdir()
                    if PROPERTY_CANDIDATE_PATTERN.fullmatch(child.name) is not None
                    and child.is_dir()
                    and not child.is_symlink()
                    and child.resolve().parent == candidate.resolve()
                )
        except OSError:
            continue
    return tuple(sorted(residue, key=lambda path: str(path)))


def retry_case_cleanup(cases_root: Path | None = None) -> tuple[int, int]:
    """Retry safe removal of interrupted case lifecycle directories."""

    effective_root = (cases_root or CASES_ROOT).resolve()
    removed = 0
    failed = 0
    for residue in list_case_cleanup_residue(effective_root):
        match = CASE_RESIDUE_PATTERN.fullmatch(residue.name)
        if match is not None:
            case_id = match.group("case_id")
            expected_parent = effective_root
        elif (
            PROPERTY_CANDIDATE_PATTERN.fullmatch(residue.name) is not None
            and CASE_ID_PATTERN.fullmatch(residue.parent.name) is not None
        ):
            case_id = residue.parent.name
            expected_parent = effective_root / case_id
        else:  # Defensive; discovery already enforces these shapes.
            continue
        try:
            with _case_id_lock(effective_root, case_id):
                if residue.resolve().parent != expected_parent.resolve() or residue.is_symlink():
                    raise ValueError("The cleanup target is outside the local cases directory.")
                shutil.rmtree(residue)
            removed += 1
        except (OSError, ValueError):
            failed += 1
    return removed, failed


def _case_paths(root: Path, case_id: str) -> CasePaths:
    return CasePaths(
        case_id=case_id,
        root=root,
        original=root / "original",
        processed=root / "processed",
        output=root / "output",
        extracted_json=root / "extracted.json",
        final_json=root / "final.json",
    )


@contextmanager
def _case_id_lock(cases_root: Path, case_id: str) -> Iterator[None]:
    if CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ValueError("The case identifier is invalid.")
    cases_root.mkdir(parents=True, exist_ok=True)
    stream = _try_lock_file(cases_root / f".case-lock-{case_id}")
    if stream is None:
        raise CaseBusyError(
            "This case is being changed in another application tab. Wait for it to finish and retry."
        )
    try:
        yield
    finally:
        _unlock_file(stream)


def _try_lock_file(path: Path) -> BinaryIO | None:
    stream = path.open("a+b")
    stream.seek(0, 2)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        return None
    return stream


def _unlock_file(stream: BinaryIO) -> None:
    try:
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


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
