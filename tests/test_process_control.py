import subprocess
import sys

from process_control import process_matches, terminate_process_tree


def test_process_match_rejects_wrong_creation_time() -> None:
    assert process_matches(
        1,
        created_at=0,
    ) is False


def test_terminate_process_tree_stops_an_exact_test_process() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process_matches(process.pid, required_arguments=("-c",)) is True

        terminate_process_tree(process.pid, timeout_seconds=2)

        assert process.wait(timeout=2) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
