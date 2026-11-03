"""HawkOffer Desktop Process Manager and Runtime Supervisor.

Provides cross-platform management for the local FastAPI/Uvicorn background daemon,
including single-instance PID locking, bounded readiness verification, clean signal
handling, and graceful shutdown coordination.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.core.config import settings

logger = logging.getLogger("hawkoffer.desktop_runtime")


@dataclass(frozen=True)
class ReadinessResult:
    """Readiness probe evaluation result."""

    ready: bool
    status_code: int | None
    latency_ms: float
    message: str
    details: dict[str, Any] | None = None


class DesktopRuntimeManager:
    """Supervisor for the local HawkOffer backend daemon process."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.host = host or settings.api_host
        self.port = port or settings.api_port
        self.data_dir = data_dir or settings.ensure_data_dir()
        self.pid_file = self.data_dir / "hawkoffer.pid"

    @property
    def health_url(self) -> str:
        """Fully-qualified local health check URL."""
        return f"http://{self.host}:{self.port}/api/v1/health"

    @property
    def system_status_url(self) -> str:
        """Fully-qualified local system status URL."""
        return f"http://{self.host}:{self.port}/api/v1/system/status"

    def read_pid(self) -> int | None:
        """Read recorded PID from lockfile if present and valid."""
        if not self.pid_file.exists():
            return None
        try:
            content = self.pid_file.read_text(encoding="utf-8").strip()
            if not content:
                return None
            return int(content)
        except (ValueError, OSError) as exc:
            logger.warning("Invalid or unreadable PID file at %s: %s", self.pid_file, exc)
            return None

    def write_pid(self, pid: int) -> None:
        """Atomically record daemon PID to lockfile."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp_file = self.pid_file.with_suffix(".tmp")
        try:
            temp_file.write_text(str(pid), encoding="utf-8")
            temp_file.replace(self.pid_file)
        except OSError as exc:
            logger.error("Failed to write PID file %s: %s", self.pid_file, exc)
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise

    def remove_pid(self) -> None:
        """Remove the PID lockfile if it exists."""
        try:
            if self.pid_file.exists():
                self.pid_file.unlink()
        except OSError as exc:
            logger.warning("Could not remove PID file %s: %s", self.pid_file, exc)

    @staticmethod
    def is_process_alive(pid: int) -> bool:
        """Check if a process with given PID is currently active."""
        if pid <= 0:
            return False

        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            windll = getattr(ctypes, "windll", None)
            if windll is None:
                return False
            kernel32 = windll.kernel32
            process_query_limited_info = 0x1000
            still_active = 259

            handle = kernel32.OpenProcess(process_query_limited_info, False, wintypes.DWORD(pid))
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return bool(exit_code.value == still_active)
                return False
            finally:
                kernel32.CloseHandle(handle)
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError as err:
                # EPERM means process exists but owned by someone else; ESRCH means no such process
                import errno

                return err.errno == errno.EPERM

    def check_readiness(self, timeout_seconds: float = 1.5) -> ReadinessResult:
        """Probe the backend /health endpoint and evaluate readiness."""
        req = Request(
            self.health_url,
            headers={"User-Agent": "HawkOffer-DesktopSupervisor/1.0"},
            method="GET",
        )
        start_time = time.perf_counter()
        try:
            with urlopen(req, timeout=timeout_seconds) as response:
                latency = (time.perf_counter() - start_time) * 1000.0
                status_code = response.status
                raw_body = response.read().decode("utf-8")
                try:
                    payload = json.loads(raw_body)
                except json.JSONDecodeError:
                    return ReadinessResult(
                        ready=False,
                        status_code=status_code,
                        latency_ms=latency,
                        message="Endpoint returned non-JSON payload",
                    )

                is_healthy = (
                    status_code == 200
                    and payload.get("database") == "connected"
                    and payload.get("status") in ("healthy", "degraded")
                )

                msg = (
                    "Backend is ready"
                    if is_healthy
                    else f"Backend reported unhealthy state: {payload}"
                )
                return ReadinessResult(
                    ready=is_healthy,
                    status_code=status_code,
                    latency_ms=latency,
                    message=msg,
                    details=payload,
                )
        except URLError as exc:
            latency = (time.perf_counter() - start_time) * 1000.0
            return ReadinessResult(
                ready=False,
                status_code=None,
                latency_ms=latency,
                message=f"Connection failed: {exc.reason}",
            )
        except Exception as exc:
            latency = (time.perf_counter() - start_time) * 1000.0
            return ReadinessResult(
                ready=False,
                status_code=None,
                latency_ms=latency,
                message=f"Readiness probe error: {exc}",
            )

    def wait_for_readiness(
        self,
        timeout_seconds: float = 20.0,
        interval_seconds: float = 0.5,
    ) -> ReadinessResult:
        """Poll the backend health endpoint until ready or timeout expires."""
        deadline = time.time() + timeout_seconds
        last_result = ReadinessResult(
            ready=False,
            status_code=None,
            latency_ms=0.0,
            message="Readiness probe did not execute",
        )

        while time.time() < deadline:
            last_result = self.check_readiness(timeout_seconds=min(1.5, interval_seconds))
            if last_result.ready:
                return last_result
            time.sleep(interval_seconds)

        return last_result

    def start(
        self,
        wait: bool = True,
        timeout_seconds: float = 20.0,
    ) -> tuple[bool, str]:
        """Start the Python background daemon if not already running."""
        # Step 1: Check if backend is already listening and healthy
        initial_check = self.check_readiness(timeout_seconds=1.0)
        if initial_check.ready:
            pid = self.read_pid()
            logger.info("Backend is already running (PID=%s, URL=%s)", pid, self.health_url)
            return True, f"Backend is already running and ready (PID {pid or 'unknown'})"

        # Step 2: Check existing PID lockfile
        existing_pid = self.read_pid()
        if existing_pid is not None and self.is_process_alive(existing_pid):
            logger.info(
                "Process %s found running. Waiting for readiness...",
                existing_pid,
            )
            if wait:
                res = self.wait_for_readiness(timeout_seconds=timeout_seconds)
                if res.ready:
                    return True, f"Existing backend process {existing_pid} became ready"
                return False, f"Existing backend process {existing_pid} failed readiness check"
            return True, f"Existing backend process {existing_pid} is active"

        # Clean up stale PID file if process is dead
        if existing_pid is not None:
            logger.info("Cleaning up stale PID file for inactive process %s", existing_pid)
            self.remove_pid()

        # Step 3: Launch new daemon process
        # Explicitly bind to 127.0.0.1 for desktop security
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--log-level",
            settings.log_level.lower(),
        ]

        logger.info("Spawning HawkOffer daemon: %s", " ".join(cmd))

        # Launch detached from current terminal on Windows, new session on Unix
        creationflags = 0
        kwargs: dict[str, Any] = {
            "cwd": str(Path(__file__).resolve().parent.parent),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }

        if sys.platform == "win32":
            # CREATE_NO_WINDOW = 0x08000000
            creationflags = 0x08000000
            kwargs["creationflags"] = creationflags
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **kwargs)
        self.write_pid(proc.pid)
        logger.info("Spawned daemon with PID %s", proc.pid)

        if not wait:
            return True, f"Backend spawned with PID {proc.pid}"

        # Step 4: Await readiness
        readiness = self.wait_for_readiness(timeout_seconds=timeout_seconds)
        if readiness.ready:
            logger.info("Backend daemon PID %s successfully verified ready", proc.pid)
            return True, f"Backend daemon started and verified ready (PID {proc.pid})"

        # Startup failure cleanup
        logger.error(
            "Backend daemon PID %s failed to become ready within %ss: %s",
            proc.pid,
            timeout_seconds,
            readiness.message,
        )
        self.stop(force=True)
        return False, f"Backend failed readiness probe: {readiness.message}"

    def stop(self, timeout_seconds: float = 8.0, force: bool = False) -> tuple[bool, str]:
        """Gracefully shut down the running backend daemon."""
        pid = self.read_pid()
        if pid is None:
            # Check if listening without PID
            res = self.check_readiness(timeout_seconds=0.5)
            if not res.ready and res.status_code is None:
                return True, "Backend is not running"
            return False, "Backend is listening but no PID file was found"

        if not self.is_process_alive(pid):
            self.remove_pid()
            return True, f"Cleaned up stale PID {pid}"

        logger.info("Stopping daemon process PID %s...", pid)

        # 1. Graceful signal
        try:
            if sys.platform == "win32":
                # On Windows, SIGTERM terminates the process cleanly
                os.kill(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            logger.warning("Could not send termination signal to PID %s: %s", pid, exc)

        # 2. Wait with bounded timeout
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not self.is_process_alive(pid):
                self.remove_pid()
                logger.info("Daemon PID %s terminated gracefully", pid)
                return True, f"Daemon PID {pid} stopped cleanly"
            time.sleep(0.3)

        # 3. Last-resort fallback force kill
        logger.warning("Daemon PID %s did not terminate in time. Forcing termination...", pid)
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            logger.error("Failed to force kill process %s: %s", pid, exc)

        self.remove_pid()
        return True, f"Daemon PID {pid} terminated"

    def status(self) -> dict[str, Any]:
        """Collect diagnostic status of the desktop daemon."""
        pid = self.read_pid()
        alive = self.is_process_alive(pid) if pid else False
        readiness = self.check_readiness(timeout_seconds=1.0)

        return {
            "host": self.host,
            "port": self.port,
            "pid": pid,
            "process_alive": alive,
            "ready": readiness.ready,
            "latency_ms": round(readiness.latency_ms, 2),
            "status_code": readiness.status_code,
            "message": readiness.message,
            "pid_file": str(self.pid_file),
            "data_dir": str(self.data_dir),
            "details": readiness.details,
        }


def main() -> int:
    """CLI entrypoint for managing the HawkOffer desktop daemon."""
    parser = argparse.ArgumentParser(
        description="HawkOffer Desktop Process Manager and Runtime Supervisor",
    )
    parser.add_argument(
        "action",
        choices=["start", "stop", "restart", "status", "readiness"],
        help="Action to perform",
    )
    parser.add_argument("--host", default=None, help="Host to bind/probe")
    parser.add_argument("--port", type=int, default=None, help="Port to bind/probe")
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Startup/probe timeout in seconds",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not wait for readiness on start",
    )

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mgr = DesktopRuntimeManager(host=args.host, port=args.port)

    if args.action == "start":
        success, msg = mgr.start(wait=not args.no_wait, timeout_seconds=args.timeout)
        print(msg)
        return 0 if success else 1

    if args.action == "stop":
        success, msg = mgr.stop()
        print(msg)
        return 0 if success else 1

    if args.action == "restart":
        mgr.stop()
        time.sleep(1.0)
        success, msg = mgr.start(wait=not args.no_wait, timeout_seconds=args.timeout)
        print(msg)
        return 0 if success else 1

    if args.action == "status":
        info = mgr.status()
        print(json.dumps(info, indent=2))
        return 0 if info["ready"] else (0 if info["process_alive"] else 1)

    if args.action == "readiness":
        res = mgr.check_readiness()
        print(f"Ready: {res.ready} | Status: {res.status_code} | Latency: {res.latency_ms:.1f}ms")
        print(f"Message: {res.message}")
        return 0 if res.ready else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
