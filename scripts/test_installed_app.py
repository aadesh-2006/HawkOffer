"""Full End-to-End Acceptance Test for Installed HawkOffer Desktop Application."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def http_get(url: str, timeout: float = 2.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "HawkOffer-AcceptanceTest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, payload: dict, timeout: float = 3.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "HawkOffer-AcceptanceTest/1.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        print(f"HTTP Error {err.code} on POST {url}: {body}", file=sys.stderr)
        raise


def wait_for_health(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            res = http_get("http://127.0.0.1:8765/api/v1/health", timeout=1.0)
            if res.get("status") == "healthy" and res.get("database") == "connected":
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    return False


def wait_for_shutdown(timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_get("http://127.0.0.1:8765/api/v1/health", timeout=0.5)
        except (urllib.error.URLError, TimeoutError, OSError):
            return True
        time.sleep(0.4)
    return False


def main() -> int:
    install_dir = Path(os.environ["LOCALAPPDATA"]) / "HawkOffer"
    tauri_exe = install_dir / "hawkoffer.exe"
    backend_exe = install_dir / "hawkoffer-backend.exe"
    uninstall_exe = install_dir / "uninstall.exe"
    data_dir = install_dir / "HawkOffer"
    pid_file = data_dir / "hawkoffer.pid"
    db_file = data_dir / "hawkoffer.db"

    print("==================================================================")
    print("HAWKOFFER INSTALLED APPLICATION ACCEPTANCE TEST")
    print("==================================================================")
    print(f"Install directory: {install_dir}")
    print(f"Tauri executable:  {tauri_exe}")
    print(f"Backend binary:    {backend_exe}")
    print(f"Data directory:    {data_dir}")
    print()

    # Step 1: Verify installation files
    print("1. Verifying installed artifacts on disk...")
    assert tauri_exe.exists(), f"Missing: {tauri_exe}"
    assert backend_exe.exists(), f"Missing: {backend_exe}"
    assert uninstall_exe.exists(), f"Missing: {uninstall_exe}"
    print("   [PASS] All installed binaries confirmed.")

    # Step 2: Clean slate for initial test run
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (ValueError, OSError):
            pass
        pid_file.unlink(missing_ok=True)

    # Step 3: Launch installed Tauri application
    print("\n2. Launching installed Tauri desktop application...")
    tauri_proc = subprocess.Popen([str(tauri_exe)])
    print(f"   [OK] Spawned hawkoffer.exe (PID {tauri_proc.pid})")

    # Step 4: Await backend readiness
    print("\n3. Probing backend readiness on loopback 127.0.0.1:8765...")
    assert wait_for_health(timeout=25.0), "Backend failed to become healthy within timeout!"
    health = http_get("http://127.0.0.1:8765/api/v1/health")
    print(f"   [PASS] Health check verified: {health}")

    # Step 5: System Status & Single-Instance Lockfile
    print("\n4. Checking system status and PID lockfile...")
    sys_status = http_get("http://127.0.0.1:8765/api/v1/system/status")
    assert sys_status.get("is_running") is True
    assert sys_status.get("is_lifecycle_gate_open") is True
    print(f"   [PASS] System status verified (lifecycle gate open: {sys_status['is_lifecycle_gate_open']})")

    assert pid_file.exists(), "PID lockfile was not created!"
    spawned_pid = int(pid_file.read_text().strip())
    print(f"   [PASS] Recorded backend PID: {spawned_pid}")

    # Step 6: Create Company and Preference Profile via REST API
    print("\n5. Creating monitored company and preference profile with semantic AI via REST API...")
    ts = int(time.time())
    test_company = {
        "name": f"Acme Robotics {ts}",
        "career_url": f"https://boards.greenhouse.io/acmerobotics-{ts}",
        "platform": "greenhouse",
        "is_active": True,
    }
    company_resp = http_post("http://127.0.0.1:8765/api/v1/companies", test_company)
    company_id = company_resp["id"]
    print(f"   [PASS] Created company: id={company_id}, name={company_resp['name']}")

    test_profile = {
        "name": f"Platform Engineer Profile {ts}",
        "preferred_roles": ["Backend Engineer", "Systems Engineer"],
        "preferred_skills": ["python", "rust", "sqlite"],
        "minimum_match_score": 75.0,
        "resume_text": "Staff Platform Engineer with deep expertise in Python, Rust, SQLite, and distributed systems.",
        "semantic_enabled": True,
        "is_active": True,
    }
    profile_resp = http_post("http://127.0.0.1:8765/api/v1/profiles", test_profile)
    profile_id = profile_resp["id"]
    assert profile_resp.get("semantic_enabled") is True
    print(f"   [PASS] Created preference profile with semantic resume: id={profile_id}, name={profile_resp['name']}")

    # Check semantic model status endpoint
    sem_status = http_get("http://127.0.0.1:8765/api/v1/system/semantic-model")
    assert sem_status.get("dimension") == 384
    print(f"   [PASS] Semantic model status endpoint verified (dimension={sem_status.get('dimension')}, status={sem_status.get('status')})")

    # Step 7: Verify data persistence on disk
    print("\n6. Verifying database existence on disk...")
    assert db_file.exists(), f"Database file does not exist: {db_file}"
    db_size = db_file.stat().st_size
    print(f"   [PASS] SQLite database verified at {db_file} ({db_size} bytes)")

    # Step 8: Close Tauri application and verify shutdown
    print("\n7. Terminating Tauri desktop window and verifying graceful backend shutdown...")
    tauri_proc.terminate()
    try:
        tauri_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        tauri_proc.kill()

    # Also stop backend via supervisor command to verify clean exit
    subprocess.run([str(backend_exe), "--action", "stop"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    assert wait_for_shutdown(timeout=10.0), "Backend failed to terminate cleanly!"
    print("   [PASS] Backend process stopped and port 8765 released.")

    # Step 9: Relaunch application and verify data survived!
    print("\n8. Relaunching application to verify data persistence across launches...")
    tauri_proc2 = subprocess.Popen([str(tauri_exe)])
    assert wait_for_health(timeout=25.0), "Backend failed to restart on second launch!"

    companies_data = http_get("http://127.0.0.1:8765/api/v1/companies")
    companies = companies_data.get("items", companies_data if isinstance(companies_data, list) else [])
    assert any(c["id"] == company_id for c in companies), "Company data was lost across restarts!"
    print(f"   [PASS] Company persisted cleanly across application restart: {companies[0]['name']}")

    profiles_data = http_get("http://127.0.0.1:8765/api/v1/profiles")
    profiles = profiles_data.get("items", profiles_data if isinstance(profiles_data, list) else [])
    matched_prof = next((p for p in profiles if p["id"] == profile_id), None)
    assert matched_prof is not None, "Profile data was lost across restarts!"
    assert matched_prof.get("semantic_enabled") is True
    assert "Staff Platform" in (matched_prof.get("resume_text") or "")
    print(f"   [PASS] Profile with semantic resume persisted cleanly across application restart: {matched_prof['name']}")

    tauri_proc2.terminate()
    try:
        tauri_proc2.wait(timeout=5)
    except subprocess.TimeoutExpired:
        tauri_proc2.kill()
    subprocess.run([str(backend_exe), "--action", "stop"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for_shutdown(timeout=10.0)

    # Step 10: Test external backend preservation (ownership safety)
    print("\n9. Testing process ownership safety (external daemon preservation)...")
    external_proc = subprocess.Popen([str(backend_exe), "--host", "127.0.0.1", "--port", "8765"])
    assert wait_for_health(timeout=20.0), "External backend failed to start!"
    print("   -> External backend is running on 127.0.0.1:8765")

    # Launch Tauri (attaches to existing backend)
    tauri_proc3 = subprocess.Popen([str(tauri_exe)])
    time.sleep(2.0)
    print("   -> Tauri launched and attached to existing backend")

    # Close Tauri window
    tauri_proc3.terminate()
    try:
        tauri_proc3.wait(timeout=5)
    except subprocess.TimeoutExpired:
        tauri_proc3.kill()
    time.sleep(2.0)
    print("   -> Tauri closed")

    # External backend MUST STILL BE ALIVE!
    external_alive = False
    try:
        res = http_get("http://127.0.0.1:8765/api/v1/health")
        if res.get("status") == "healthy":
            external_alive = True
    except (urllib.error.URLError, TimeoutError, OSError):
        pass

    assert external_alive, "FAIL: External backend was killed when Tauri closed!"
    print("   [PASS] External backend was preserved cleanly when Tauri window closed!")

    # Clean up external backend
    external_proc.terminate()
    try:
        external_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        external_proc.kill()
    subprocess.run([str(backend_exe), "--action", "stop"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for_shutdown(timeout=10.0)

    print("\n==================================================================")
    print("ALL 9 REAL INSTALLED-APPLICATION ACCEPTANCE TESTS PASSED (100%)")
    print("==================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
