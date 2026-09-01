"""Windows desktop entry point for the packaged Streamlit application."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from process_control import (
    ProcessControlError,
    current_process_created_at,
    process_matches,
    terminate_process_tree,
)
from runtime_paths import DATA_ROOT, RESOURCE_ROOT, ensure_runtime_directories, is_frozen


APP_STATUS_PATH = DATA_ROOT / "desktop-status.json"
INSTANCE_LOCK_PATH = DATA_ROOT / "desktop-instance.lock"
STARTUP_LOG_PATH = DATA_ROOT / "startup.log"
PORT_RANGE = range(8501, 8511)
STATUS_READ_RETRIES = 10
STARTUP_GRACE_SECONDS = 120
EXISTING_APP_WAIT_SECONDS = 10
HEALTH_POLL_SECONDS = 0.25


class _AlreadyRunning(RuntimeError):
    pass


class _StartupCancelled(RuntimeError):
    pass


def main() -> int:
    ensure_runtime_directories()
    _configure_frozen_stdio()
    _log("Launcher started.")
    if "--rms-worker" in sys.argv[1:]:
        return _run_rms_worker_mode(sys.argv[1:])
    if "--package-smoke-check" in sys.argv[1:]:
        try:
            return _run_package_smoke_check()
        except Exception:
            _log(f"Packaged dependency smoke check failed:\n{traceback.format_exc()}")
            return 1
    try:
        return _run_desktop_app()
    except _AlreadyRunning:
        _log("Opened the already running application.")
        return 0
    except _StartupCancelled:
        _log("Startup cancelled; the unresponsive application was left running.")
        return 0
    except Exception as error:
        _log(f"Startup failed:\n{traceback.format_exc()}")
        _show_error(f"Yavlena KYC Manager could not start.\n\n{error}")
        return 1


def _run_desktop_app() -> int:
    instance_lock = _try_acquire_instance_lock()
    if instance_lock is None:
        existing_url, recovered_lock = _wait_for_existing_app_or_lock()
        if existing_url:
            webbrowser.open(existing_url)
            raise _AlreadyRunning
        if recovered_lock is None:
            status = _read_app_status(discard_invalid=False)
            if status is None or not _app_process_matches_status(status):
                raise RuntimeError(
                    "Another application instance owns the local lock but could not be verified "
                    "safely. Close Yavlena KYC Manager in Task Manager or restart Windows."
                )
            if not _confirm_force_close_app():
                raise _StartupCancelled
            _force_close_owned_app(status)
            recovered_lock = _wait_for_instance_lock()
            if recovered_lock is None:
                raise RuntimeError(
                    "The unresponsive application was closed, but its operating-system lock "
                    "was not released. Restart Windows before trying again."
                )
        instance_lock = recovered_lock

    try:
        existing_url = _active_app_url()
        if existing_url:
            webbrowser.open(existing_url)
            return 0

        port = _available_port()
        token = uuid.uuid4().hex
        _acquire_app_status(port, token)
        url = f"http://127.0.0.1:{port}"
        _log(f"Starting the local application at {url}.")
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
        try:
            from streamlit.web import cli as streamlit_cli

            script_path = RESOURCE_ROOT / "streamlit_app.py"
            if not script_path.is_file():
                raise RuntimeError("The packaged Streamlit entry point is missing.")
            sys.argv = [
                "streamlit",
                "run",
                str(script_path),
                f"--server.port={port}",
                "--server.address=127.0.0.1",
                "--server.headless=true",
                "--global.developmentMode=false",
                "--browser.gatherUsageStats=false",
                "--client.showErrorDetails=false",
                "--server.maxUploadSize=25",
                "--theme.base=light",
                "--theme.primaryColor=#3B8122",
                "--theme.backgroundColor=#F3F5F2",
                "--theme.secondaryBackgroundColor=#E8EDE6",
                "--theme.textColor=#24364B",
            ]
            result = int(streamlit_cli.main() or 0)
            _log(f"Streamlit stopped with exit code {result}.")
            return result
        finally:
            _release_app_status(token)
    finally:
        _release_instance_lock(instance_lock)


def _run_rms_worker_mode(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--rms-worker", action="store_true")
    parser.add_argument("final_json", type=Path)
    parser.add_argument("status_path", type=Path)
    parser.add_argument("snapshot_sha256")
    parser.add_argument("lock_path", type=Path)
    parser.add_argument("lock_token")
    parsed = parser.parse_args(arguments)

    from website import _run_rms_worker

    _run_rms_worker(
        parsed.final_json,
        parsed.status_path,
        parsed.snapshot_sha256,
        parsed.lock_path,
        parsed.lock_token,
    )
    return 0


def _active_app_url() -> str:
    status = _read_app_status()
    if status is None:
        return ""
    pid = status.get("pid")
    port = status.get("port")
    token = status.get("token")
    valid = (
        isinstance(pid, int)
        and isinstance(port, int)
        and port in PORT_RANGE
        and isinstance(token, str)
        and bool(token)
    )
    if valid and _app_status_owner_is_live(status):
        url = f"http://127.0.0.1:{port}"
        if _url_is_healthy(url):
            return url
    _discard_app_status(token if isinstance(token, str) else None)
    return ""


def _wait_for_existing_app() -> str:
    """Wait for the lock-owning launcher to publish a healthy local endpoint."""

    url, recovered_lock = _wait_for_existing_app_or_lock(recover_lock=False)
    if recovered_lock is not None:  # Defensive: disabled above.
        _release_instance_lock(recovered_lock)
    return url


def _wait_for_existing_app_or_lock(
    *,
    recover_lock: bool = True,
) -> tuple[str, BinaryIO | None]:
    """Wait for a healthy owner, or take over promptly if that owner exits."""

    deadline = time.monotonic() + EXISTING_APP_WAIT_SECONDS
    while time.monotonic() < deadline:
        status = _read_app_status(discard_invalid=False)
        if status is not None:
            pid = status.get("pid")
            port = status.get("port")
            token = status.get("token")
            valid = (
                isinstance(pid, int)
                and isinstance(port, int)
                and port in PORT_RANGE
                and isinstance(token, str)
                and bool(token)
            )
            if valid and _app_status_owner_is_live(status):
                url = f"http://127.0.0.1:{port}"
                if _url_is_healthy(url):
                    return url, None
        if recover_lock:
            recovered_lock = _try_acquire_instance_lock()
            if recovered_lock is not None:
                return "", recovered_lock
        time.sleep(HEALTH_POLL_SECONDS)
    return "", None


def _read_app_status(*, discard_invalid: bool = True) -> dict[str, object] | None:
    """Read the exclusive launcher record, tolerating its brief write window."""

    for attempt in range(STATUS_READ_RETRIES):
        try:
            value = json.loads(APP_STATUS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            if attempt + 1 < STATUS_READ_RETRIES:
                time.sleep(0.05)
                continue
            if discard_invalid:
                _discard_app_status()
            return None
        except OSError as error:
            raise RuntimeError("The local application status file cannot be read.") from error
        if isinstance(value, dict):
            return value
        if discard_invalid:
            _discard_app_status()
        return None
    return None


def _discard_app_status(expected_token: str | None = None) -> None:
    if expected_token is not None:
        try:
            current = json.loads(APP_STATUS_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        except OSError as error:
            raise RuntimeError("The local application status file cannot be read.") from error
        if not isinstance(current, dict) or current.get("token") != expected_token:
            return
    try:
        APP_STATUS_PATH.unlink(missing_ok=True)
    except OSError as error:
        raise RuntimeError("The stale local application status file cannot be removed.") from error


def _url_is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/_stcore/health", timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def _available_port() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No local application port is available (8501-8510).")


def _acquire_app_status(port: int, token: str) -> None:
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "port": port,
            "token": token,
            "mode": "packaged" if is_frozen() else "source",
            "process_created_at": current_process_created_at(),
            "started_at": time.time(),
        }
    ).encode("utf-8")
    try:
        descriptor = os.open(APP_STATUS_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        existing_url = _active_app_url()
        if existing_url:
            webbrowser.open(existing_url)
            raise _AlreadyRunning from error
        try:
            descriptor = os.open(APP_STATUS_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as race_error:
            raise _AlreadyRunning from race_error
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def _release_app_status(token: str) -> None:
    try:
        status = json.loads(APP_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if status.get("token") == token:
        APP_STATUS_PATH.unlink(missing_ok=True)


def _app_process_matches_status(status: dict[str, object]) -> bool:
    """Verify the exact status owner, including PID creation time, before termination."""

    pid = status.get("pid")
    token = status.get("token")
    mode = status.get("mode")
    created_at = status.get("process_created_at")
    expected_mode = "packaged" if is_frozen() else "source"
    if (
        not isinstance(pid, int)
        or not isinstance(token, str)
        or not token
        or mode != expected_mode
        or not isinstance(created_at, (int, float))
    ):
        return False
    if is_frozen():
        return process_matches(
            pid,
            executable=Path(sys.executable).resolve(),
            created_at=float(created_at),
        )
    return process_matches(
        pid,
        required_arguments=((RESOURCE_ROOT / "desktop_launcher.py").resolve(),),
        created_at=float(created_at),
    )


def _app_status_owner_is_live(status: dict[str, object]) -> bool:
    """Use strong ownership for new records while tolerating older healthy releases."""

    if "mode" in status or "process_created_at" in status:
        return _app_process_matches_status(status)
    pid = status.get("pid")
    return isinstance(pid, int) and _process_is_running(pid)


def _force_close_owned_app(status: dict[str, object]) -> None:
    if not _app_process_matches_status(status):
        raise RuntimeError(
            "The existing process changed before it could be verified, so it was not closed."
        )
    pid = status["pid"]
    token = status["token"]
    assert isinstance(pid, int) and isinstance(token, str)
    try:
        terminate_process_tree(pid)
    except ProcessControlError as error:
        raise RuntimeError(str(error)) from error
    _discard_app_status(token)
    _log(f"Force-closed verified unresponsive application process {pid}.")


def _wait_for_instance_lock(timeout_seconds: float = 10.0) -> BinaryIO | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        recovered = _try_acquire_instance_lock()
        if recovered is not None:
            return recovered
        time.sleep(HEALTH_POLL_SECONDS)
    return None


def _open_when_ready(url: str) -> None:
    deadline = time.monotonic() + STARTUP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if _url_is_healthy(url):
            _log("Local application health check succeeded.")
            if os.getenv("YAVLENA_KYC_NO_BROWSER", "").strip() != "1":
                webbrowser.open(url)
            return
        time.sleep(HEALTH_POLL_SECONDS)


def _try_acquire_instance_lock() -> BinaryIO | None:
    """Hold one byte exclusively for the lifetime of the desktop server."""

    stream = INSTANCE_LOCK_PATH.open("a+b")
    stream.seek(0, os.SEEK_END)
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


def _release_instance_lock(stream: BinaryIO) -> None:
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


def _run_package_smoke_check() -> int:
    """Exercise packaged OCR inference and the bundled browser without network access."""

    from PIL import Image, ImageDraw
    from playwright.sync_api import sync_playwright

    from ocr import PaddleOcrEngine
    from runtime_paths import BUNDLED_PLAYWRIGHT_ROOT, configure_packaged_browser

    image_path = DATA_ROOT / ".package-smoke-check.png"
    try:
        image = Image.new("RGB", (640, 320), "white")
        ImageDraw.Draw(image).text((40, 130), "SYNTHETIC PACKAGE CHECK", fill="black")
        image.save(image_path)
        PaddleOcrEngine().recognize([image_path])
    finally:
        image_path.unlink(missing_ok=True)

    configure_packaged_browser()
    browser_executable = next(
        BUNDLED_PLAYWRIGHT_ROOT.glob("chromium-*/chrome-win*/chrome.exe"),
        None,
    )
    if browser_executable is None:
        raise RuntimeError("The bundled headed Chromium executable is missing.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_executable),
            headless=True,
        )
        try:
            page = browser.new_page()
            page.set_content("<title>Package check</title><p>ready</p>")
            if page.title() != "Package check":
                raise RuntimeError("The bundled Chromium runtime did not render the smoke page.")
        finally:
            browser.close()
    _log("Packaged OCR and Chromium smoke check succeeded.")
    return 0


def schedule_desktop_shutdown(delay_seconds: float = 0.75) -> None:
    """Remove this process's launcher record and stop after the UI response is sent."""

    status = _read_app_status()
    if status is not None and status.get("pid") == os.getpid():
        token = status.get("token")
        _discard_app_status(token if isinstance(token, str) else None)
    _log("Desktop shutdown requested by the operator.")
    timer = threading.Timer(delay_seconds, os._exit, args=(0,))
    timer.daemon = True
    timer.start()


def _configure_frozen_stdio() -> None:
    """Give windowed builds a real stderr/stdout target for library diagnostics."""

    if not getattr(sys, "frozen", False):
        return
    stream = STARTUP_LOG_PATH.open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _log(message: str) -> None:
    try:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with STARTUP_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                and exit_code.value == 259
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _show_error(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Yavlena KYC Manager", 0x10)
    else:
        print(message, file=sys.stderr)


def _confirm_force_close_app() -> bool:
    message = (
        "Yavlena KYC Manager is already running but is not responding.\n\n"
        "Force-close the verified application process and start a new instance?\n\n"
        "Any operation currently in progress will be interrupted."
    )
    if sys.platform == "win32":
        import ctypes

        # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2
        return ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "Yavlena KYC Manager",
            0x134,
        ) == 6
    return False


if __name__ == "__main__":
    raise SystemExit(main())
