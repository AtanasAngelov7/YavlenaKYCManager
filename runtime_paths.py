"""Stable resource and writable-data locations for source and packaged execution."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_DIRECTORY_NAME = "YavlenaKYCManager"
RESOURCE_ROOT = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _default_data_root() -> Path:
    override = os.getenv("YAVLENA_KYC_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not is_frozen():
        return RESOURCE_ROOT
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / APP_DIRECTORY_NAME).resolve()


DATA_ROOT = _default_data_root()
CASES_ROOT = DATA_ROOT / "cases"
SETTINGS_PATH = DATA_ROOT / ".env"
OCR_CACHE_ROOT = DATA_ROOT / ".local" / "paddlex"
BUNDLED_OCR_CACHE_ROOT = RESOURCE_ROOT / "bundled_assets" / "paddlex"
BUNDLED_PLAYWRIGHT_ROOT = RESOURCE_ROOT / "playwright-browsers"


def ensure_runtime_directories() -> None:
    for directory in (DATA_ROOT, CASES_ROOT, OCR_CACHE_ROOT):
        directory.mkdir(parents=True, exist_ok=True)


def prepare_ocr_cache() -> Path:
    """Seed the writable OCR cache from packaged models without downloading."""

    OCR_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    if BUNDLED_OCR_CACHE_ROOT.is_dir() and BUNDLED_OCR_CACHE_ROOT != OCR_CACHE_ROOT:
        shutil.copytree(BUNDLED_OCR_CACHE_ROOT, OCR_CACHE_ROOT, dirs_exist_ok=True)
    return OCR_CACHE_ROOT


def configure_packaged_browser() -> None:
    if BUNDLED_PLAYWRIGHT_ROOT.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BUNDLED_PLAYWRIGHT_ROOT)
        os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
