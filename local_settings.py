"""Atomic updates for the local per-user environment settings file."""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Mapping


_SETTINGS_WRITE_LOCK = threading.RLock()


def _serialized_env_value(value: str) -> str:
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError("Local setting values must fit on one line.")
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def update_env_values(env_path: Path, values: Mapping[str, str]) -> None:
    if not values:
        return
    for name, value in values.items():
        _validate_env_name(name)
        _serialized_env_value(value)
    with _SETTINGS_WRITE_LOCK:
        lines = _read_env_lines(env_path)
        updated = _updated_lines(lines, values)
        _write_env_lines(env_path, updated)
        os.environ.update(values)


def remove_env_values(env_path: Path, names: tuple[str, ...]) -> None:
    """Remove selected settings without disturbing unrelated local values."""

    if not names:
        return
    for name in names:
        _validate_env_name(name)
    with _SETTINGS_WRITE_LOCK:
        lines = _read_env_lines(env_path)
        pattern = _assignment_pattern(names)
        updated = [line for line in lines if pattern.match(line.strip()) is None]
        _write_env_lines(env_path, updated)
        for name in names:
            os.environ.pop(name, None)


def _validate_env_name(name: str) -> None:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
        raise ValueError("Local setting names must be valid environment variable names.")


def _assignment_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        rf"^(?:export\s+)?({'|'.join(re.escape(name) for name in names)})\s*="
    )


def _read_env_lines(env_path: Path) -> list[str]:
    return env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []


def _updated_lines(lines: list[str], values: Mapping[str, str]) -> list[str]:
    pattern = _assignment_pattern(tuple(values))
    updated: list[str] = []
    replaced: set[str] = set()
    for line in lines:
        match = pattern.match(line.strip())
        if match is None:
            updated.append(line)
            continue
        name = match.group(1)
        if name not in replaced:
            updated.append(f"{name}={_serialized_env_value(values[name])}")
            replaced.add(name)
    if updated and updated[-1].strip():
        updated.append("")
    for name, value in values.items():
        if name not in replaced:
            updated.append(f"{name}={_serialized_env_value(value)}")
    return updated


def _write_env_lines(env_path: Path, lines: list[str]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = env_path.with_name(f".{env_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        content = "\n".join(lines).rstrip()
        temporary.write_text(f"{content}\n" if content else "", encoding="utf-8")
        for attempt in range(20):
            try:
                temporary.replace(env_path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
