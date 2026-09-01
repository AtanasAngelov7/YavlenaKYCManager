from pathlib import Path

import runtime_paths


def test_explicit_data_directory_overrides_source_and_packaged_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured = tmp_path / "operator-data"
    monkeypatch.setenv("YAVLENA_KYC_DATA_DIR", str(configured))

    assert runtime_paths._default_data_root() == configured.resolve()


def test_packaged_data_defaults_to_local_app_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("YAVLENA_KYC_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: True)

    assert runtime_paths._default_data_root() == (tmp_path / "YavlenaKYCManager").resolve()


def test_packaged_ocr_assets_seed_the_writable_cache(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "bundle" / "paddlex"
    writable = tmp_path / "data" / "paddlex"
    model = bundled / "official_models" / "synthetic-model" / "model.json"
    model.parent.mkdir(parents=True)
    model.write_text('{"synthetic": true}', encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "BUNDLED_OCR_CACHE_ROOT", bundled)
    monkeypatch.setattr(runtime_paths, "OCR_CACHE_ROOT", writable)

    result = runtime_paths.prepare_ocr_cache()

    assert result == writable
    assert (writable / "official_models" / "synthetic-model" / "model.json").read_text(
        encoding="utf-8"
    ) == '{"synthetic": true}'


def test_packaged_browser_overrides_an_external_playwright_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled = tmp_path / "bundle" / "playwright-browsers"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(runtime_paths, "BUNDLED_PLAYWRIGHT_ROOT", bundled)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "external-cache"))

    runtime_paths.configure_packaged_browser()

    assert runtime_paths.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(bundled)
    assert runtime_paths.os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"
