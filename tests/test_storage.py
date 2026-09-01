import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
import storage

from storage import (
    CaseBusyError,
    CaseCleanupError,
    archive_property_artifacts,
    case_mutation_lock,
    create_case,
    delete_local_case,
    existing_case_paths,
    file_sha256,
    list_local_cases,
    list_case_cleanup_residue,
    promote_property_candidate,
    retry_case_cleanup,
    save_original,
    write_json,
)


def test_create_case_uses_safe_local_name(tmp_path: Path) -> None:
    paths, source = create_case("../../Person Name.jpg", b"image", tmp_path / "cases")

    assert source.parent == paths.original
    assert source.name == "Person-Name.jpg"
    assert source.read_bytes() == b"image"
    assert paths.processed.is_dir()
    assert paths.output.is_dir()


def test_create_case_rejects_unknown_extension(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JPEG, PNG, and PDF"):
        create_case("document.exe", b"content", tmp_path / "cases")


def test_case_can_store_named_front_and_back(tmp_path: Path) -> None:
    paths, front = create_case(
        "camera-front.jpg",
        b"front",
        tmp_path / "cases",
        storage_stem="front",
    )
    back = save_original(paths, "camera-back.png", b"back", storage_stem="back")

    assert front.name == "front.jpg"
    assert back.name == "back.png"
    assert front.read_bytes() == b"front"
    assert back.read_bytes() == b"back"


def test_property_artifacts_are_recoverably_archived_before_replacement(tmp_path: Path) -> None:
    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    property_source = save_original(
        paths,
        "deed.pdf",
        b"old deed",
        storage_stem="property-document",
    )
    property_record = paths.root / "property_extracted.json"
    property_record.write_text('{"old": true}', encoding="utf-8")
    property_pages = paths.processed / "property"
    property_pages.mkdir()
    (property_pages / "page-1.png").write_bytes(b"old page")

    archived = archive_property_artifacts(paths)

    assert not property_source.exists()
    assert not property_record.exists()
    assert not property_pages.exists()
    assert len(archived) == 3
    assert all(path.exists() for path in archived)
    assert any(path.read_bytes() == b"old deed" for path in archived if path.is_file())


def test_property_promotion_rolls_back_every_completed_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    active_source = save_original(
        paths,
        "old.pdf",
        b"old deed",
        storage_stem="property-document",
    )
    active_record = paths.root / "property_extracted.json"
    active_record.write_text('{"version": "old"}', encoding="utf-8")
    active_processed = paths.processed / "property"
    active_processed.mkdir()
    (active_processed / "page-1.png").write_bytes(b"old page")

    staging = paths.root / ".property-candidate-test"
    candidate_source = staging / "original" / "property-document.pdf"
    candidate_source.parent.mkdir(parents=True)
    candidate_source.write_bytes(b"new deed")
    candidate_processed = staging / "processed-property"
    candidate_processed.mkdir()
    (candidate_processed / "page-1.png").write_bytes(b"new page")

    real_replace = storage._replace_path
    calls = 0

    def fail_first_promotion(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(storage, "_replace_path", fail_first_promotion)

    with pytest.raises(OSError, match="simulated promotion failure"):
        promote_property_candidate(
            paths,
            candidate_source,
            candidate_processed,
            {"version": "new"},
            replace_source=True,
        )

    assert active_source.read_bytes() == b"old deed"
    assert active_record.read_text(encoding="utf-8") == '{"version": "old"}'
    assert (active_processed / "page-1.png").read_bytes() == b"old page"
    assert candidate_source.read_bytes() == b"new deed"
    assert (candidate_processed / "page-1.png").read_bytes() == b"new page"
    assert not list(paths.root.glob("property-extracted-replaced-*.json"))
    assert not list(paths.root.glob(".property-extracted-*.json"))


def test_replace_path_retries_transient_windows_permission_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("content", encoding="utf-8")
    real_replace = Path.replace
    attempts = 0

    def transient_replace(path: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("synthetic transient file lock")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", transient_replace)
    monkeypatch.setattr(storage.time, "sleep", lambda delay: None)

    storage._replace_path(source, destination)

    assert attempts == 3
    assert destination.read_text(encoding="utf-8") == "content"


def test_property_promotion_stages_its_private_record_inside_the_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    staging = paths.root / ".property-candidate-test"
    candidate_source = staging / "original" / "property-document.pdf"
    candidate_source.parent.mkdir(parents=True)
    candidate_source.write_bytes(b"new deed")
    candidate_processed = staging / "processed-property"
    candidate_processed.mkdir()
    captured_moves: list[tuple[Path, Path]] = []

    def stop_before_promotion(moves: list[tuple[Path, Path]]) -> None:
        captured_moves.extend(moves)
        raise OSError("simulated interruption")

    monkeypatch.setattr(storage, "_replace_all_or_rollback", stop_before_promotion)

    with pytest.raises(OSError, match="simulated interruption"):
        promote_property_candidate(
            paths,
            candidate_source,
            candidate_processed,
            {"private": "property data"},
            replace_source=True,
        )

    assert any(source == staging / "property_extracted.json" for source, _ in captured_moves)
    assert not list(paths.root.glob(".property-extracted-*.json"))


def test_same_property_source_versions_only_derived_artifacts(tmp_path: Path) -> None:
    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    active_source = save_original(
        paths,
        "deed.pdf",
        b"same deed",
        storage_stem="property-document",
    )
    write_json(paths.root / "property_extracted.json", {"version": "old"})
    active_processed = paths.processed / "property"
    active_processed.mkdir()
    (active_processed / "page-1.png").write_bytes(b"old page")

    staging = paths.root / ".property-candidate-test"
    candidate_source = staging / "original" / "property-document.pdf"
    candidate_source.parent.mkdir(parents=True)
    candidate_source.write_bytes(b"same deed")
    candidate_processed = staging / "processed-property"
    candidate_processed.mkdir()
    (candidate_processed / "page-1.png").write_bytes(b"new page")

    promoted_source, promoted_processed = promote_property_candidate(
        paths,
        candidate_source,
        candidate_processed,
        {"version": "new"},
        replace_source=False,
    )

    assert promoted_source == active_source.resolve()
    assert active_source.read_bytes() == b"same deed"
    assert candidate_source.is_file()
    assert (promoted_processed / "page-1.png").read_bytes() == b"new page"
    assert json.loads((paths.root / "property_extracted.json").read_text(encoding="utf-8")) == {
        "version": "new"
    }
    assert list(paths.root.glob("property-extracted-replaced-*.json"))
    assert list(paths.processed.glob("property-replaced-*"))
    assert not list(paths.original.glob("property-document-replaced-*"))


def test_preserved_property_source_requires_the_same_filename_and_bytes(tmp_path: Path) -> None:
    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    save_original(paths, "deed.pdf", b"same bytes", storage_stem="property-document")
    staging = paths.root / ".property-candidate-test"
    candidate_source = staging / "original" / "property-document.png"
    candidate_source.parent.mkdir(parents=True)
    candidate_source.write_bytes(b"same bytes")
    candidate_processed = staging / "processed-property"
    candidate_processed.mkdir()

    with pytest.raises(ValueError, match="identical active property source"):
        promote_property_candidate(
            paths,
            candidate_source,
            candidate_processed,
            {"version": "new"},
            replace_source=False,
        )

    assert (paths.original / "property-document.pdf").read_bytes() == b"same bytes"
    assert not (paths.root / "property_extracted.json").exists()
    assert not list(paths.root.glob(".property-extracted-*.json"))


def test_write_json_preserves_cyrillic(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    write_json(destination, {"name": "ИВАН"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"name": "ИВАН"}


def test_file_sha256_hashes_the_stored_source_bytes(tmp_path: Path) -> None:
    source = tmp_path / "property-document.pdf"
    source.write_bytes(b"authorized synthetic notary document")

    assert file_sha256(source) == (
        "fec68d7dba0b3ed7a81502e095615eaff8918fdf518fb1c57d7d99b620733078"
    )


def test_existing_case_paths_reconstructs_only_the_expected_case(tmp_path: Path) -> None:
    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")

    reconstructed = existing_case_paths(paths.root, paths.case_id)

    assert reconstructed == paths
    with pytest.raises(ValueError, match="invalid"):
        existing_case_paths(paths.root, "different-case")


def test_local_cases_are_discovered_without_reading_personal_data(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    older, _ = create_case("front.jpg", b"older", cases_root)
    newer, _ = create_case("front.jpg", b"newer", cases_root)
    (cases_root / "not-a-case").mkdir()
    (cases_root / "not-a-case" / "extracted.json").write_text("{}", encoding="utf-8")
    older.root.touch()
    newer.root.touch()

    discovered = list_local_cases(cases_root)

    assert {paths.case_id for paths in discovered} == {older.case_id, newer.case_id}
    assert all(paths.original.is_dir() for paths in discovered)


def test_local_case_deletion_is_confined_to_direct_case_children(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    paths, _ = create_case("front.jpg", b"synthetic identity", cases_root)
    outside_paths, _ = create_case("front.jpg", b"outside", tmp_path / "outside")

    delete_local_case(paths, cases_root)

    assert not paths.root.exists()
    with pytest.raises(ValueError, match="outside the local cases directory"):
        delete_local_case(outside_paths, cases_root)
    assert outside_paths.root.is_dir()


def test_case_creation_exposes_cleanup_residue_when_private_file_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    real_rmtree = storage.shutil.rmtree
    monkeypatch.setattr(Path, "write_bytes", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(storage.shutil, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("locked")))

    with pytest.raises(CaseCleanupError, match="temporary personal files"):
        create_case("front.jpg", b"private", cases_root)

    residue = list_case_cleanup_residue(cases_root)
    assert len(residue) == 1
    monkeypatch.setattr(storage.shutil, "rmtree", real_rmtree)
    removed, failed = retry_case_cleanup(cases_root)
    assert (removed, failed) == (1, 0)


def test_interrupted_delete_retires_case_and_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    paths, _ = create_case("front.jpg", b"private", cases_root)
    real_rmtree = storage.shutil.rmtree
    monkeypatch.setattr(storage.shutil, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("locked")))

    with pytest.raises(CaseCleanupError, match="pending cleanup"):
        delete_local_case(paths, cases_root)

    assert not paths.root.exists()
    assert len(list_case_cleanup_residue(cases_root)) == 1
    monkeypatch.setattr(storage.shutil, "rmtree", real_rmtree)
    assert retry_case_cleanup(cases_root) == (1, 0)


def test_abandoned_property_candidate_is_discovered_and_removed(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    paths, _ = create_case("front.jpg", b"private", cases_root)
    candidate = paths.root / ".property-candidate-abandoned"
    candidate.mkdir()
    (candidate / "property-document.pdf").write_bytes(b"private deed")

    assert list_case_cleanup_residue(cases_root) == (candidate,)
    assert retry_case_cleanup(cases_root) == (1, 0)
    assert not candidate.exists()
    assert paths.root.is_dir()


def test_case_mutation_lock_rejects_a_second_session(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    paths, _ = create_case("front.jpg", b"private", cases_root)
    locked = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with case_mutation_lock(paths, cases_root):
            locked.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert locked.wait(timeout=5)
    try:
        with pytest.raises(CaseBusyError, match="another application tab"):
            with case_mutation_lock(paths, cases_root):
                pass
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_case_mutation_lock_revalidates_the_case_after_lock_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    paths, _ = create_case("front.jpg", b"private", cases_root)

    @contextmanager
    def deletion_between_checks(root: Path, case_id: str):
        storage.shutil.rmtree(paths.root)
        yield

    monkeypatch.setattr(storage, "_case_id_lock", deletion_between_checks)

    with pytest.raises(ValueError, match="changed or was removed"):
        with case_mutation_lock(paths, cases_root):
            pytest.fail("a retired case must not be exposed for mutation")


def test_delete_rechecks_external_blocker_inside_case_lock(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    paths, _ = create_case("front.jpg", b"private", cases_root)

    with pytest.raises(CaseBusyError, match="active RMS browser"):
        delete_local_case(paths, cases_root, blocked=lambda: True)

    assert paths.root.is_dir()


def test_write_json_removes_temporary_file_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "record.json"

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_json(destination, {"personal": "стойност"})

    assert not list(tmp_path.glob(".record.json.*.tmp"))


def test_write_json_uses_independent_atomic_temporary_files(tmp_path: Path) -> None:
    destination = tmp_path / "shared-status.json"
    errors: list[Exception] = []

    def writer(worker: int) -> None:
        for iteration in range(50):
            try:
                write_json(destination, {"worker": worker, "iteration": iteration})
            except Exception as error:  # pragma: no cover - collected for the assertion
                errors.append(error)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(8)))

    assert errors == []
    assert destination.is_file()
    assert not list(tmp_path.glob(".shared-status.json.*.tmp"))
