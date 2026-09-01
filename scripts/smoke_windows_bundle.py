"""End-to-end smoke check for the frozen Windows application bundle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


STARTUP_TIMEOUT_SECONDS = 180


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--browser-root", type=Path, required=True)
    arguments = parser.parse_args()

    executable = arguments.executable.resolve()
    browser_executable = next(
        arguments.browser_root.resolve().glob("chromium-*/chrome-win*/chrome.exe"),
        None,
    )
    if not executable.is_file():
        raise RuntimeError(f"Packaged executable is missing: {executable}")
    if browser_executable is None:
        raise RuntimeError("The smoke-test Chromium executable is missing.")

    with tempfile.TemporaryDirectory(prefix="yavlena-package-smoke-") as temporary:
        data_root = Path(temporary) / "data"
        environment = os.environ.copy()
        environment["YAVLENA_KYC_DATA_DIR"] = str(data_root)
        environment["YAVLENA_KYC_NO_BROWSER"] = "1"

        dependency_check = subprocess.run(
            [str(executable), "--package-smoke-check"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=STARTUP_TIMEOUT_SECONDS,
            check=False,
        )
        if dependency_check.returncode != 0:
            startup_log = data_root / "startup.log"
            diagnostic = (
                startup_log.read_text(encoding="utf-8", errors="replace")
                if startup_log.is_file()
                else "The packaged startup log was not created."
            )
            raise RuntimeError(
                "Frozen OCR inference or bundled Chromium initialization failed.\n"
                f"{diagnostic}"
            )

        process = subprocess.Popen(
            [str(executable)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = _wait_for_healthy_url(process, data_root / "desktop-status.json")
            _verify_rendered_ui_and_exit(url, browser_executable)
            process.wait(timeout=20)
            if process.returncode != 0:
                raise RuntimeError(
                    f"The packaged application exited with code {process.returncode}."
                )
            if (data_root / "desktop-status.json").exists():
                raise RuntimeError("The packaged application left stale desktop status state.")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    print("Packaged OCR, Chromium, Streamlit UI, and shutdown smoke check succeeded.")
    return 0


def _wait_for_healthy_url(process: subprocess.Popen[bytes], status_path: Path) -> str:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"The packaged application stopped during startup with code {process.returncode}."
            )
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            port = status.get("port") if isinstance(status, dict) else None
            if isinstance(port, int):
                url = f"http://127.0.0.1:{port}"
                with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        return url
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        time.sleep(0.25)
    raise RuntimeError("The packaged application did not become healthy in time.")


def _verify_rendered_ui_and_exit(url: str, browser_executable: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_executable),
            headless=True,
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            expect(page.get_by_text("Yavlena KYC Manager", exact=True).first).to_be_visible(
                timeout=120_000
            )
            upload_button = page.locator(
                '[data-testid="stFileUploaderDropzone"] '
                'button[data-testid^="stBaseButton-secondary"]'
            ).first
            expect(upload_button).to_be_visible(timeout=30_000)
            upload_colors = upload_button.evaluate(
                "element => [getComputedStyle(element).backgroundColor, "
                "getComputedStyle(element).color]"
            )
            if upload_colors != ["rgb(59, 129, 34)", "rgb(255, 255, 255)"]:
                raise RuntimeError(
                    "The packaged upload button did not receive the accessible primary styling: "
                    f"{upload_colors}"
                )

            extract_button = page.get_by_role("button", name="Extract both sides")
            expect(extract_button).to_be_disabled(timeout=30_000)
            disabled_colors = extract_button.evaluate(
                "element => [getComputedStyle(element).backgroundColor, "
                "getComputedStyle(element).color]"
            )
            if disabled_colors != ["rgb(226, 231, 224)", "rgb(89, 102, 89)"]:
                raise RuntimeError(
                    "The packaged disabled action did not receive the expected muted styling: "
                    f"{disabled_colors}"
                )

            exit_button = page.get_by_role("button", name="Exit application")
            expect(exit_button).to_be_visible(timeout=30_000)
            try:
                exit_button.click(timeout=30_000)
                page.wait_for_timeout(1_000)
            except Exception:
                # The server intentionally closes the page connection shortly after the click.
                pass
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
