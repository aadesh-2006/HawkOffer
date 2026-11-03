"""Tests for the desktop process manager and runtime supervisor."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from app.desktop_runtime import DesktopRuntimeManager, ReadinessResult


@pytest.fixture
def temp_runtime_dir(tmp_path: Path) -> Path:
    """Create an isolated temporary data directory."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


@pytest.fixture
def runtime_manager(temp_runtime_dir: Path) -> DesktopRuntimeManager:
    """Instantiate a DesktopRuntimeManager configured with the temporary directory."""
    return DesktopRuntimeManager(
        host="127.0.0.1",
        port=8765,
        data_dir=temp_runtime_dir,
    )


def test_pid_lifecycle(runtime_manager: DesktopRuntimeManager) -> None:
    """Test reading, writing, and removing PID lockfile."""
    assert runtime_manager.read_pid() is None

    my_pid = os.getpid()
    runtime_manager.write_pid(my_pid)
    assert runtime_manager.read_pid() == my_pid
    assert runtime_manager.pid_file.exists()

    runtime_manager.remove_pid()
    assert runtime_manager.read_pid() is None
    assert not runtime_manager.pid_file.exists()


def test_pid_file_malformed(runtime_manager: DesktopRuntimeManager) -> None:
    """Ensure malformed or empty PID files are handled safely."""
    runtime_manager.pid_file.write_text("not-a-number\n", encoding="utf-8")
    assert runtime_manager.read_pid() is None

    runtime_manager.pid_file.write_text("", encoding="utf-8")
    assert runtime_manager.read_pid() is None


def test_is_process_alive_current_and_invalid(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify process liveness checks for current and invalid PIDs."""
    assert runtime_manager.is_process_alive(os.getpid()) is True
    assert runtime_manager.is_process_alive(-1) is False
    assert runtime_manager.is_process_alive(0) is False
    # Very high PID that is guaranteed not to exist
    assert runtime_manager.is_process_alive(99999999) is False


def test_check_readiness_healthy(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify readiness probe when backend returns healthy status."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = (
        b'{"status":"healthy","database":"connected","scheduler":"running","timestamp":"2026-01-01T00:00:00Z"}'
    )
    mock_resp.__enter__.return_value = mock_resp

    with patch("app.desktop_runtime.urlopen", return_value=mock_resp):
        res = runtime_manager.check_readiness(timeout_seconds=1.0)
        assert res.ready is True
        assert res.status_code == 200
        assert res.details is not None
        assert res.details["status"] == "healthy"


def test_check_readiness_database_disconnected(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify readiness probe fails when database is disconnected."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = (
        b'{"status":"degraded","database":"disconnected","scheduler":"running"}'
    )
    mock_resp.__enter__.return_value = mock_resp

    with patch("app.desktop_runtime.urlopen", return_value=mock_resp):
        res = runtime_manager.check_readiness(timeout_seconds=1.0)
        assert res.ready is False
        assert "unhealthy" in res.message


def test_check_readiness_connection_refused(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify readiness probe returns non-ready when server is unreachable."""
    with patch("app.desktop_runtime.urlopen", side_effect=URLError("Connection refused")):
        res = runtime_manager.check_readiness(timeout_seconds=1.0)
        assert res.ready is False
        assert res.status_code is None
        assert "Connection failed" in res.message


def test_check_readiness_invalid_json(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify non-JSON response is rejected gracefully."""
    mock_resp = MagicMock()
    mock_resp.status = 502
    mock_resp.read.return_value = b"<html>Bad Gateway</html>"
    mock_resp.__enter__.return_value = mock_resp

    with patch("app.desktop_runtime.urlopen", return_value=mock_resp):
        res = runtime_manager.check_readiness(timeout_seconds=1.0)
        assert res.ready is False
        assert "non-JSON" in res.message


def test_wait_for_readiness_success(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify bounded wait succeeds when readiness is confirmed."""
    with patch.object(
        runtime_manager,
        "check_readiness",
        return_value=ReadinessResult(
            ready=True,
            status_code=200,
            latency_ms=5.0,
            message="Ready",
        ),
    ):
        res = runtime_manager.wait_for_readiness(timeout_seconds=2.0, interval_seconds=0.1)
        assert res.ready is True


def test_wait_for_readiness_timeout(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify bounded wait times out when probe consistently fails."""
    with patch.object(
        runtime_manager,
        "check_readiness",
        return_value=ReadinessResult(
            ready=False,
            status_code=None,
            latency_ms=5.0,
            message="Offline",
        ),
    ):
        res = runtime_manager.wait_for_readiness(timeout_seconds=0.3, interval_seconds=0.1)
        assert res.ready is False


def test_start_already_running(runtime_manager: DesktopRuntimeManager) -> None:
    """Ensure duplicate backend processes are not started if one is already ready."""
    with patch.object(
        runtime_manager,
        "check_readiness",
        return_value=ReadinessResult(
            ready=True,
            status_code=200,
            latency_ms=2.0,
            message="Ready",
        ),
    ):
        success, msg = runtime_manager.start(wait=True)
        assert success is True
        assert "already running" in msg


def test_start_cleans_stale_pid_and_spawns(runtime_manager: DesktopRuntimeManager) -> None:
    """Ensure dead stale PIDs are cleared prior to starting."""
    runtime_manager.write_pid(99999999)  # Inactive PID

    mock_proc = MagicMock()
    mock_proc.pid = 12345

    with (
        patch.object(
            runtime_manager,
            "check_readiness",
            side_effect=[
                ReadinessResult(ready=False, status_code=None, latency_ms=0.0, message="Offline"),
                ReadinessResult(ready=True, status_code=200, latency_ms=1.0, message="Ready"),
            ],
        ),
        patch("subprocess.Popen", return_value=mock_proc),
    ):
        success, msg = runtime_manager.start(wait=True, timeout_seconds=2.0)
        assert success is True
        assert runtime_manager.read_pid() == 12345
        assert "started and verified ready" in msg


def test_stop_not_running(runtime_manager: DesktopRuntimeManager) -> None:
    """Stopping a non-running daemon succeeds gracefully."""
    with patch.object(
        runtime_manager,
        "check_readiness",
        return_value=ReadinessResult(ready=False, status_code=None, latency_ms=0.0, message="Down"),
    ):
        success, msg = runtime_manager.stop()
        assert success is True
        assert "not running" in msg


def test_status_diagnostics(runtime_manager: DesktopRuntimeManager) -> None:
    """Verify structured diagnostics output."""
    runtime_manager.write_pid(os.getpid())

    with patch.object(
        runtime_manager,
        "check_readiness",
        return_value=ReadinessResult(
            ready=True,
            status_code=200,
            latency_ms=12.5,
            message="Ready",
            details={"database": "connected"},
        ),
    ):
        info = runtime_manager.status()
        assert info["pid"] == os.getpid()
        assert info["process_alive"] is True
        assert info["ready"] is True
        assert info["latency_ms"] == 12.5
        assert info["details"]["database"] == "connected"
        assert info["owns_process"] is False


def test_process_ownership_reused_instance_preserves_external_daemon(
    runtime_manager: DesktopRuntimeManager,
) -> None:
    """Reusing an already running backend marks process unowned and preserves it on stop."""
    external_pid = os.getpid()
    runtime_manager.write_pid(external_pid)

    with patch.object(
        runtime_manager,
        "check_readiness",
        return_value=ReadinessResult(
            ready=True,
            status_code=200,
            latency_ms=1.5,
            message="Ready",
            details={"database": "connected"},
        ),
    ):
        success, msg = runtime_manager.start(wait=True)
        assert success is True
        assert "already running" in msg
        assert runtime_manager.owns_process is False

        # Attempt stop with only_if_owned=True (e.g. desktop window close)
        stop_success, stop_msg = runtime_manager.stop(only_if_owned=True)
        assert stop_success is True
        assert "preserved" in stop_msg
        # External PID file must remain intact
        assert runtime_manager.read_pid() == external_pid


def test_process_ownership_spawned_instance_terminates_on_stop(
    runtime_manager: DesktopRuntimeManager,
) -> None:
    """Spawning a new backend marks process owned and allows clean stop."""
    mock_proc = MagicMock()
    mock_proc.pid = 98765

    with (
        patch.object(
            runtime_manager,
            "check_readiness",
            side_effect=[
                ReadinessResult(ready=False, status_code=None, latency_ms=0.0, message="Offline"),
                ReadinessResult(ready=True, status_code=200, latency_ms=1.0, message="Ready"),
            ],
        ),
        patch("subprocess.Popen", return_value=mock_proc),
    ):
        success, msg = runtime_manager.start(wait=True, timeout_seconds=2.0)
        assert success is True
        assert runtime_manager.owns_process is True
        assert runtime_manager.read_pid() == 98765

    with (
        patch.object(runtime_manager, "is_process_alive", side_effect=[True, False]),
        patch("os.kill") as mock_kill,
    ):
        stop_success, stop_msg = runtime_manager.stop(only_if_owned=True)
        assert stop_success is True
        assert "stopped cleanly" in stop_msg
        assert mock_kill.called
        assert runtime_manager.read_pid() is None
        assert runtime_manager.owns_process is False
