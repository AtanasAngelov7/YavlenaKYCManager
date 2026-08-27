import json
from pathlib import Path

import pytest

from storage import create_case, save_original, write_json


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


def test_write_json_preserves_cyrillic(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    write_json(destination, {"name": "ИВАН"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"name": "ИВАН"}
