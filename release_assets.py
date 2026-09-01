"""Deterministic verification of large assets embedded in Windows releases."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


OCR_MODEL_NAMES = (
    "PP-OCRv5_mobile_det",
    "cyrillic_PP-OCRv5_mobile_rec",
    "PP-LCNet_x1_0_doc_ori",
)
RELEASE_ASSET_MANIFEST = Path("packaging") / "release-assets.json"


@dataclass(frozen=True)
class ReleaseAsset:
    path: Path
    recursive: bool


def release_assets(project_root: Path) -> dict[str, ReleaseAsset]:
    """Return the exact model and browser directories included in a release."""

    root = project_root.resolve()
    model_root = root / ".local" / "paddlex" / "official_models"
    assets = {
        f"ocr/{name}": ReleaseAsset(model_root / name, recursive=False)
        for name in OCR_MODEL_NAMES
    }
    browser_root = root / "packaging" / "playwright-browsers"
    browser_directories = sorted(
        directory
        for directory in browser_root.glob("chromium-*")
        if directory.is_dir()
        and any(directory.glob("chrome-win*/chrome.exe"))
    )
    if len(browser_directories) != 1:
        raise ValueError("Exactly one packaged Chromium release is required.")
    browser_directory = browser_directories[0]
    assets[f"browser/{browser_directory.name}"] = ReleaseAsset(
        browser_directory,
        recursive=True,
    )
    return assets


def asset_sha256(asset: ReleaseAsset) -> str:
    """Hash a canonical inventory of relative names, sizes, and file contents."""

    root = asset.path.resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Release asset directory is missing or unsafe: {asset.path}")
    candidates = root.rglob("*") if asset.recursive else root.iterdir()
    files = sorted(
        (path for path in candidates if path.is_file() and not path.is_symlink()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not files:
        raise ValueError(f"Release asset directory is empty: {asset.path}")
    inventory: list[tuple[str, int, str]] = []
    for path in files:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        inventory.append(
            (path.relative_to(root).as_posix(), path.stat().st_size, digest.hexdigest())
        )
    payload = json.dumps(inventory, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_release_assets(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, ReleaseAsset]:
    """Reject a release build unless every embedded asset matches its reviewed hash."""

    root = project_root.resolve()
    manifest = (manifest_path or root / RELEASE_ASSET_MANIFEST).resolve()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        expected = payload["assets"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("The release-asset manifest is missing or invalid.") from error
    if not isinstance(expected, dict) or any(
        not isinstance(name, str)
        or not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for name, value in expected.items()
    ):
        raise ValueError("The release-asset manifest contains invalid entries.")

    assets = release_assets(root)
    if set(expected) != set(assets):
        raise ValueError("The release-asset manifest does not match the packaged asset set.")
    mismatched = [
        name
        for name, asset in assets.items()
        if asset_sha256(asset) != expected[name]
    ]
    if mismatched:
        raise ValueError(
            "Release assets changed without a reviewed manifest update: "
            + ", ".join(mismatched)
        )
    return assets


def model_runtime_files(asset: ReleaseAsset) -> tuple[Path, ...]:
    """Return only top-level runtime model files, excluding download metadata."""

    if asset.recursive:
        raise ValueError("An OCR model asset must use a top-level file inventory.")
    return tuple(
        sorted(
            (
                path
                for path in asset.path.iterdir()
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.name,
        )
    )
