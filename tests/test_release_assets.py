import json
from pathlib import Path

import pytest

from release_assets import (
    OCR_MODEL_NAMES,
    RELEASE_ASSET_MANIFEST,
    asset_sha256,
    model_runtime_files,
    release_assets,
    verify_release_assets,
)


def _release_tree(root: Path) -> None:
    model_root = root / ".local" / "paddlex" / "official_models"
    for name in OCR_MODEL_NAMES:
        directory = model_root / name
        directory.mkdir(parents=True)
        (directory / "inference.json").write_text(name, encoding="utf-8")
        cache = directory / ".cache"
        cache.mkdir()
        (cache / "download-metadata").write_text("not packaged", encoding="utf-8")
    browser = root / "packaging" / "playwright-browsers" / "chromium-test"
    executable = browser / "chrome-win64" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"browser")


def test_release_assets_are_hash_pinned_and_exclude_model_download_metadata(
    tmp_path: Path,
) -> None:
    _release_tree(tmp_path)
    assets = release_assets(tmp_path)
    manifest = tmp_path / RELEASE_ASSET_MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {"version": 1, "assets": {name: asset_sha256(asset) for name, asset in assets.items()}},
            indent=2,
        ),
        encoding="utf-8",
    )

    verified = verify_release_assets(tmp_path)

    assert set(verified) == set(assets)
    assert all(
        all(path.parent.name != ".cache" for path in model_runtime_files(verified[f"ocr/{name}"]))
        for name in OCR_MODEL_NAMES
    )


def test_release_asset_verification_rejects_changed_runtime_bytes(tmp_path: Path) -> None:
    _release_tree(tmp_path)
    assets = release_assets(tmp_path)
    manifest = tmp_path / RELEASE_ASSET_MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"assets": {name: asset_sha256(asset) for name, asset in assets.items()}}),
        encoding="utf-8",
    )
    next(iter(model_runtime_files(assets[f"ocr/{OCR_MODEL_NAMES[0]}"]))).write_text(
        "changed",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="changed without a reviewed manifest update"):
        verify_release_assets(tmp_path)
