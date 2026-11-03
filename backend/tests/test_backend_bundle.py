"""Test suite for HawkOffer standalone bundled backend executable.

Verifies:
1. Standalone executable exists in dist/ and sidecar directory.
2. Executable starts in an isolated temporary data directory without Python dependencies.
3. Database schemas and tables are initialized correctly.
4. HTTP health endpoint responds with 'database: connected' and 'scheduler: running'.
5. Single-instance PID locking prevents duplicate backend daemon execution.
6. Graceful shutdown terminates process and cleans up PID file.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest


def get_backend_executable_path() -> Path:
    """Resolve location of compiled standalone backend executable."""
    backend_dir = Path(__file__).resolve().parent.parent
    repo_root = backend_dir.parent

    # Check dist directory
    dist_exe = backend_dir / "dist" / "hawkoffer-backend.exe"
    if dist_exe.exists():
        return dist_exe

    # Check Tauri binaries directory
    sidecar_exe = (
        repo_root / "src-tauri" / "binaries" / "hawkoffer-backend-x86_64-pc-windows-msvc.exe"
    )
    if sidecar_exe.exists():
        return sidecar_exe

    pytest.skip("Standalone backend executable not found. Run scripts/build_backend.py first.")


def test_bundled_backend_executable_exists() -> None:
    """Verify that the compiled standalone binary exists and is of non-trivial size."""
    exe_path = get_backend_executable_path()
    assert exe_path.exists()
    assert exe_path.is_file()
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    assert size_mb > 10.0, f"Executable size {size_mb:.2f} MB is unexpectedly small"


def test_bundled_backend_status_command(tmp_path: Path) -> None:
    """Verify executing --action status against an idle directory returns offline status."""
    exe_path = get_backend_executable_path()
    cmd = [
        str(exe_path),
        "--action",
        "status",
        "--data-dir",
        str(tmp_path),
        "--port",
        "8799",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    # Status returns non-zero if offline, but stdout contains valid JSON
    assert proc.stdout.strip()
    data = json.loads(proc.stdout)
    assert "ready" in data
    assert data["ready"] is False
    assert data["process_alive"] is False


def test_bundled_backend_full_lifecycle(tmp_path: Path) -> None:
    """Verify complete launch, readiness, duplicate avoidance, and shutdown of standalone binary."""
    exe_path = get_backend_executable_path()
    test_port = 8791
    health_url = f"http://127.0.0.1:{test_port}/api/v1/health"

    data_dir = tmp_path / "bundled_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = data_dir / "hawkoffer.pid"
    db_file = data_dir / "hawkoffer.db"

    # 1. Launch standalone executable as a subprocess
    cmd = [
        str(exe_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(test_port),
        "--data-dir",
        str(data_dir),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # 2. Wait for readiness probe with bounded timeout
        deadline = time.time() + 25.0
        ready = False
        health_data = {}

        while time.time() < deadline:
            try:
                req = urllib.request.Request(
                    health_url, headers={"User-Agent": "HawkOffer-Test/1.0"}
                )
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        body = resp.read().decode("utf-8")
                        health_data = json.loads(body)
                        if health_data.get("database") == "connected":
                            ready = True
                            break
            except Exception:
                pass
            time.sleep(0.4)

        assert ready, f"Bundled backend failed to become ready within deadline. Code: {proc.poll()}"
        assert health_data["status"] == "healthy"
        assert health_data["database"] == "connected"
        assert health_data["scheduler"] == "running"

        # 3. Verify PID lockfile was written
        assert pid_file.exists()
        recorded_pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert recorded_pid > 0

        # 4. Verify SQLite database exists and was populated
        assert db_file.exists()
        assert db_file.stat().st_size > 0

        # 5. Verify single-instance duplicate avoidance
        # Launching a second instance on the same port/data-dir should detect existing backend
        dup_cmd = [
            str(exe_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(test_port),
            "--data-dir",
            str(data_dir),
        ]
        dup_proc = subprocess.run(dup_cmd, capture_output=True, text=True, timeout=10)
        assert dup_proc.returncode == 0
        assert "ALREADY_RUNNING" in dup_proc.stdout

        # 6. Verify status command detects running process
        status_cmd = [
            str(exe_path),
            "--action",
            "status",
            "--port",
            str(test_port),
            "--data-dir",
            str(data_dir),
        ]
        status_proc = subprocess.run(status_cmd, capture_output=True, text=True, timeout=10)
        assert status_proc.returncode == 0
        status_data = json.loads(status_proc.stdout)
        assert status_data["ready"] is True
        assert status_data["process_alive"] is True
        assert status_data["pid"] == recorded_pid

    finally:
        # 7. Gracefully terminate backend via supervisor stop action
        stop_cmd = [
            str(exe_path),
            "--action",
            "stop",
            "--port",
            str(test_port),
            "--data-dir",
            str(data_dir),
        ]
        stop_res = subprocess.run(stop_cmd, capture_output=True, text=True, timeout=10)
        assert stop_res.returncode == 0

        # Also wait on outer process
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        # 8. Verify clean termination and PID file cleanup
        deadline = time.time() + 5.0
        while time.time() < deadline and pid_file.exists():
            time.sleep(0.2)

        assert not pid_file.exists(), "PID lockfile should be removed after clean termination"
