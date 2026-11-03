"""Build script to compile the HawkOffer Python backend into a standalone executable.

Produces:
1. `backend/dist/hawkoffer-backend.exe` (standalone binary)
2. `src-tauri/binaries/hawkoffer-backend-x86_64-pc-windows-msvc.exe` (Tauri sidecar binary)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


def calculate_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with filepath.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    """Execute PyInstaller build and deploy sidecar binaries."""
    backend_dir = Path(__file__).resolve().parent.parent
    repo_root = backend_dir.parent
    spec_file = backend_dir / "hawkoffer-backend.spec"
    dist_dir = backend_dir / "dist"
    build_dir = backend_dir / "build"
    tauri_bin_dir = repo_root / "src-tauri" / "binaries"

    print("==================================================================")
    print("HawkOffer Standalone Backend Build Pipeline")
    print("==================================================================")
    print(f"Backend directory:  {backend_dir}")
    print(f"Specification file: {spec_file}")
    print(f"Target sidecar dir: {tauri_bin_dir}")
    print()

    if not spec_file.exists():
        print(f"ERROR: Spec file not found at {spec_file}", file=sys.stderr)
        return 1

    pyinstaller_exe = backend_dir / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller_exe.exists():
        pyinstaller_exe = Path(shutil.which("pyinstaller") or "pyinstaller")

    # 1. Clean previous build output
    print("1. Cleaning previous dist/ and build/ artifacts...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir, ignore_errors=True)
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)

    # 2. Run PyInstaller
    cmd = [
        str(pyinstaller_exe),
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]
    print(f"2. Running PyInstaller: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(backend_dir))
    if result.returncode != 0:
        print(f"ERROR: PyInstaller build failed with exit code {result.returncode}", file=sys.stderr)
        return result.returncode

    # 3. Verify output
    binary_name = "hawkoffer-backend.exe" if sys.platform == "win32" else "hawkoffer-backend"
    built_binary = dist_dir / binary_name

    if not built_binary.exists():
        print(f"ERROR: Expected binary not found at {built_binary}", file=sys.stderr)
        return 1

    size_mb = built_binary.stat().st_size / (1024 * 1024)
    sha256 = calculate_sha256(built_binary)
    print(f"3. Successfully built: {built_binary.name} ({size_mb:.2f} MB)")
    print(f"   SHA-256: {sha256}")

    # 4. Copy to Tauri sidecar directory
    print("4. Copying to Tauri sidecar directory...")
    tauri_bin_dir.mkdir(parents=True, exist_ok=True)

    # Determine sidecar filename with target triple
    target_triple = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "x86_64-unknown-linux-gnu"
    sidecar_target = tauri_bin_dir / f"hawkoffer-backend-{target_triple}.exe" if sys.platform == "win32" else tauri_bin_dir / f"hawkoffer-backend-{target_triple}"
    generic_target = tauri_bin_dir / binary_name

    shutil.copy2(built_binary, sidecar_target)
    shutil.copy2(built_binary, generic_target)

    print(f"   -> Copied: {sidecar_target}")
    print(f"   -> Copied: {generic_target}")
    print()
    print("==================================================================")
    print("Backend build completed successfully!")
    print("==================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
