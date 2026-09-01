from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from local_settings import remove_env_values, update_env_values


def test_env_values_round_trip_special_characters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RMS_PASSWORD", raising=False)
    env_path = tmp_path / ".env"
    password = "  secret # part \\ with 'quotes' and ${NAME}  "

    update_env_values(env_path, {"RMS_PASSWORD": password})

    assert dotenv_values(env_path, interpolate=False)["RMS_PASSWORD"] == password


def test_env_update_preserves_unrelated_values_and_removes_duplicates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_MODEL=old\nUNRELATED=keep\nOPENAI_MODEL=duplicate\n",
        encoding="utf-8",
    )

    update_env_values(env_path, {"OPENAI_MODEL": "gpt-test"})

    values = dotenv_values(env_path, interpolate=False)
    assert values["OPENAI_MODEL"] == "gpt-test"
    assert values["UNRELATED"] == "keep"
    assert env_path.read_text(encoding="utf-8").count("OPENAI_MODEL=") == 1


@pytest.mark.parametrize("value", ["line one\nline two", "line one\rline two", "bad\x00value"])
def test_env_update_rejects_multiline_and_nul_values(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="one line"):
        update_env_values(tmp_path / ".env", {"SETTING": value})


def test_env_removal_preserves_unrelated_values_and_clears_process_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env"
    update_env_values(
        env_path,
        {"RMS_EMAIL": "operator@example.test", "RMS_PASSWORD": "secret", "KEEP": "value"},
    )
    monkeypatch.setenv("RMS_EMAIL", "operator@example.test")
    monkeypatch.setenv("RMS_PASSWORD", "secret")

    remove_env_values(env_path, ("RMS_EMAIL", "RMS_PASSWORD"))

    values = dotenv_values(env_path, interpolate=False)
    assert values == {"KEEP": "value"}
    assert "RMS_EMAIL" not in os.environ
    assert "RMS_PASSWORD" not in os.environ


def test_concurrent_setting_updates_do_not_lose_unrelated_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    def writer(index: int) -> None:
        update_env_values(env_path, {f"SETTING_{index}": str(index)})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(20)))

    values = dotenv_values(env_path, interpolate=False)
    assert {f"SETTING_{index}": str(index) for index in range(20)}.items() <= values.items()
