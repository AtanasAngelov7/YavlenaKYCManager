import json
import os
from pathlib import Path

import desktop_launcher


def test_worker_mode_dispatches_to_the_in_process_rms_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "website._run_rms_worker",
        lambda *arguments: captured.append(arguments),
    )
    final_json = tmp_path / "case" / "final.json"
    status_path = tmp_path / "case" / "rms-status.json"
    lock_path = tmp_path / "cases" / ".rms-lock"

    result = desktop_launcher._run_rms_worker_mode(
        [
            "--rms-worker",
            str(final_json),
            str(status_path),
            "a" * 64,
            str(lock_path),
            "lock-token",
        ]
    )

    assert result == 0
    assert captured == [(final_json, status_path, "a" * 64, lock_path, "lock-token")]


def test_stale_desktop_status_is_removed(tmp_path: Path, monkeypatch) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps({"pid": 4321, "port": 8501, "token": "token"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "_process_is_running", lambda pid: False)

    assert desktop_launcher._active_app_url() == ""
    assert not status_path.exists()


def test_live_desktop_status_reuses_the_existing_local_url(tmp_path: Path, monkeypatch) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps({"pid": 4321, "port": 8507, "token": "token"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "_process_is_running", lambda pid: pid == 4321)
    monkeypatch.setattr(desktop_launcher, "_url_is_healthy", lambda url: True)

    assert desktop_launcher._active_app_url() == "http://127.0.0.1:8507"


def test_new_desktop_status_never_reuses_an_unverified_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps(
            {
                "pid": 4321,
                "port": 8507,
                "token": "token",
                "mode": "source",
                "process_created_at": 123.5,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "_process_is_running", lambda pid: True)
    monkeypatch.setattr(
        desktop_launcher,
        "_app_process_matches_status",
        lambda status: False,
    )
    monkeypatch.setattr(desktop_launcher, "_url_is_healthy", lambda url: True)

    assert desktop_launcher._active_app_url() == ""
    assert not status_path.exists()


def test_malformed_desktop_status_is_removed_and_does_not_block_acquisition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "STATUS_READ_RETRIES", 1)

    assert desktop_launcher._active_app_url() == ""
    assert not status_path.exists()

    desktop_launcher._acquire_app_status(8501, "new-token")
    assert json.loads(status_path.read_text(encoding="utf-8"))["token"] == "new-token"


def test_old_unhealthy_live_status_is_recovered(tmp_path: Path, monkeypatch) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps({"pid": 4321, "port": 8507, "token": "token"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "_process_is_running", lambda pid: True)
    monkeypatch.setattr(desktop_launcher, "_url_is_healthy", lambda url: False)

    assert desktop_launcher._active_app_url() == ""
    assert not status_path.exists()


def test_status_cleanup_does_not_remove_a_replacement_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps({"pid": 4321, "port": 8501, "token": "new-owner"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)

    desktop_launcher._discard_app_status("old-owner")

    assert json.loads(status_path.read_text(encoding="utf-8"))["token"] == "new-owner"


def test_instance_lock_is_exclusive_and_released(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_launcher,
        "INSTANCE_LOCK_PATH",
        tmp_path / "desktop-instance.lock",
    )

    first = desktop_launcher._try_acquire_instance_lock()
    assert first is not None
    try:
        assert desktop_launcher._try_acquire_instance_lock() is None
    finally:
        desktop_launcher._release_instance_lock(first)

    after_release = desktop_launcher._try_acquire_instance_lock()
    assert after_release is not None
    desktop_launcher._release_instance_lock(after_release)


def test_existing_launcher_is_opened_only_after_health_succeeds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps({"pid": 4321, "port": 8507, "token": "owner"}),
        encoding="utf-8",
    )
    health_results = iter([False, False, True])
    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "STARTUP_GRACE_SECONDS", 1)
    monkeypatch.setattr(desktop_launcher, "HEALTH_POLL_SECONDS", 0)
    monkeypatch.setattr(desktop_launcher, "_process_is_running", lambda pid: True)
    monkeypatch.setattr(
        desktop_launcher,
        "_url_is_healthy",
        lambda url: next(health_results),
    )

    assert desktop_launcher._wait_for_existing_app() == "http://127.0.0.1:8507"


def test_waiter_ignores_stale_status_while_lock_owner_recovers(monkeypatch) -> None:
    statuses = iter(
        [
            {"pid": 1111, "port": 8501, "token": "stale"},
            {"pid": 2222, "port": 8502, "token": "new-owner"},
        ]
    )
    monkeypatch.setattr(desktop_launcher, "STARTUP_GRACE_SECONDS", 1)
    monkeypatch.setattr(desktop_launcher, "HEALTH_POLL_SECONDS", 0)
    monkeypatch.setattr(
        desktop_launcher,
        "_read_app_status",
        lambda **options: next(statuses),
    )
    monkeypatch.setattr(
        desktop_launcher,
        "_process_is_running",
        lambda pid: pid == 2222,
    )
    monkeypatch.setattr(desktop_launcher, "_url_is_healthy", lambda url: True)

    assert desktop_launcher._wait_for_existing_app() == "http://127.0.0.1:8502"


def test_waiter_recovers_the_instance_lock_when_starting_owner_exits(monkeypatch) -> None:
    recovered_lock = object()
    monkeypatch.setattr(desktop_launcher, "STARTUP_GRACE_SECONDS", 1)
    monkeypatch.setattr(desktop_launcher, "HEALTH_POLL_SECONDS", 0)
    monkeypatch.setattr(desktop_launcher, "_read_app_status", lambda **options: None)
    monkeypatch.setattr(
        desktop_launcher,
        "_try_acquire_instance_lock",
        lambda: recovered_lock,
    )

    url, lock = desktop_launcher._wait_for_existing_app_or_lock()

    assert url == ""
    assert lock is recovered_lock


def test_scheduled_shutdown_removes_only_the_current_process_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_path = tmp_path / "desktop-status.json"
    status_path.write_text(
        json.dumps({"pid": os.getpid(), "port": 8501, "token": "token"}),
        encoding="utf-8",
    )
    started: list[tuple[float, object, tuple[int, ...]]] = []

    class FakeTimer:
        daemon = False

        def __init__(self, delay: float, function: object, args: tuple[int, ...]) -> None:
            started.append((delay, function, args))

        def start(self) -> None:
            return None

    monkeypatch.setattr(desktop_launcher, "APP_STATUS_PATH", status_path)
    monkeypatch.setattr(desktop_launcher, "STARTUP_LOG_PATH", tmp_path / "startup.log")
    monkeypatch.setattr(desktop_launcher.threading, "Timer", FakeTimer)

    desktop_launcher.schedule_desktop_shutdown(0.25)

    assert not status_path.exists()
    assert started == [(0.25, os._exit, (0,))]


def test_source_app_owner_requires_launcher_path_and_creation_time(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(desktop_launcher, "is_frozen", lambda: False)
    monkeypatch.setattr(
        desktop_launcher,
        "process_matches",
        lambda pid, **options: captured.append({"pid": pid, **options}) or True,
    )

    assert desktop_launcher._app_process_matches_status(
        {
            "pid": 4321,
            "token": "owner-token",
            "mode": "source",
            "process_created_at": 123.5,
        }
    ) is True

    assert captured == [
        {
            "pid": 4321,
            "required_arguments": (
                (desktop_launcher.RESOURCE_ROOT / "desktop_launcher.py").resolve(),
            ),
            "created_at": 123.5,
        }
    ]


def test_packaged_app_owner_requires_exact_executable_and_creation_time(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(desktop_launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(
        desktop_launcher,
        "process_matches",
        lambda pid, **options: captured.append({"pid": pid, **options}) or True,
    )

    assert desktop_launcher._app_process_matches_status(
        {
            "pid": 4321,
            "token": "owner-token",
            "mode": "packaged",
            "process_created_at": 123.5,
        }
    ) is True

    assert captured == [
        {
            "pid": 4321,
            "executable": Path(desktop_launcher.sys.executable).resolve(),
            "created_at": 123.5,
        }
    ]


def test_force_close_owned_app_rechecks_identity_before_terminating(monkeypatch) -> None:
    status = {
        "pid": 4321,
        "token": "owner-token",
        "mode": "source",
        "process_created_at": 123.5,
    }
    stopped: list[int] = []
    discarded: list[str] = []
    monkeypatch.setattr(desktop_launcher, "_app_process_matches_status", lambda value: True)
    monkeypatch.setattr(desktop_launcher, "terminate_process_tree", stopped.append)
    monkeypatch.setattr(desktop_launcher, "_discard_app_status", discarded.append)
    monkeypatch.setattr(desktop_launcher, "_log", lambda message: None)

    desktop_launcher._force_close_owned_app(status)

    assert stopped == [4321]
    assert discarded == ["owner-token"]


def test_source_run_script_uses_the_single_instance_launcher() -> None:
    command = (Path(__file__).parents[1] / "run.cmd").read_text(encoding="utf-8")

    assert '"%PROJECT_PYTHON%" desktop_launcher.py' in command
    assert "-m streamlit run" not in command
