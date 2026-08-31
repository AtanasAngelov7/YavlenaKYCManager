import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import storage

from storage import (
    archive_property_artifacts,
    create_case,
    existing_case_paths,
    file_sha256,
    promote_property_candidate,
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
