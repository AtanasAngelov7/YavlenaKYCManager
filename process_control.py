"""Small, verified process-inspection helpers for local recovery actions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import psutil


class ProcessControlError(RuntimeError):
    """Raised when an app-owned process cannot be inspected or stopped safely."""


def process_matches(
    pid: int,
    *,
    required_arguments: Iterable[str | Path] = (),
    executable: Path | None = None,
    created_at: float | None = None,
) -> bool:
    """Verify a live PID using immutable launch details before any termination."""

    if pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        if created_at is not None and abs(process.create_time() - created_at) > 1.0:
            return False
        if executable is not None and not _same_path(process.exe(), executable):
            return False
        arguments = tuple(process.cmdline())
    except (psutil.Error, OSError, ValueError):
        return False

    return all(
        any(_argument_matches(actual, expected) for actual in arguments)
        for expected in required_arguments
    )


def terminate_process_tree(pid: int, *, timeout_seconds: float = 5.0) -> None:
    """Terminate one previously verified process and all of its descendants."""

    if pid <= 0 or pid == os.getpid():
        raise ProcessControlError("Refusing to terminate the current or an invalid process.")
    try:
        owner = psutil.Process(pid)
        descendants = owner.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    except (psutil.AccessDenied, OSError) as error:
        raise ProcessControlError("The owned process tree could not be inspected.") from error

    targets = [*reversed(descendants), owner]
    for process in targets:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            raise ProcessControlError("Windows denied permission to close the owned process.") from error

    _, remaining = psutil.wait_procs(targets, timeout=timeout_seconds)
    for process in remaining:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            raise ProcessControlError("Windows denied permission to force-close the owned process.") from error
    _, survivors = psutil.wait_procs(remaining, timeout=timeout_seconds)
    if survivors:
        raise ProcessControlError("The owned process did not close after termination.")


def current_process_created_at() -> float:
    """Return the current process creation timestamp for PID-reuse protection."""

    try:
        return psutil.Process(os.getpid()).create_time()
    except (psutil.Error, OSError) as error:
        raise ProcessControlError("The current process identity could not be read.") from error


def _argument_matches(actual: str, expected: str | Path) -> bool:
    if isinstance(expected, Path):
        return _same_path(actual, expected)
    return actual == expected


def _same_path(actual: str, expected: Path) -> bool:
    try:
        return Path(actual).resolve() == expected.resolve()
    except (OSError, ValueError):
        return False
