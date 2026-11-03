# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for HawkOffer standalone backend executable.

Bundles FastAPI, Uvicorn, SQLAlchemy, aiosqlite, Alembic, and all monitoring
orchestration components into an isolated single-file executable for desktop deployment.
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

SPEC_DIR = Path(SPEC).resolve().parent
APP_DIR = SPEC_DIR / "app"
ALEMBIC_DIR = SPEC_DIR / "alembic"
ALEMBIC_INI = SPEC_DIR / "alembic.ini"

datas = [
    (str(ALEMBIC_DIR), "alembic"),
    (str(ALEMBIC_INI), "."),
]

# Collect all app submodules and third-party dynamic plugins
hidden_imports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "aiosqlite",
    "alembic",
    "platformdirs",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "bs4",
    "apscheduler",
    "apscheduler.schedulers.asyncio",
    "apscheduler.triggers.interval",
    "apscheduler.triggers.date",
    "numpy",
    "tokenizers",
    "onnxruntime",
]

hidden_imports += collect_submodules("app")
hidden_imports += collect_submodules("alembic")
hidden_imports += collect_submodules("tokenizers")
hidden_imports += collect_submodules("onnxruntime")

datas += collect_data_files("tokenizers")
datas += collect_data_files("onnxruntime")

a = Analysis(
    ["app/bundle_entry.py"],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pytest_asyncio",
        "pytest_cov",
        "ruff",
        "mypy",
        "tkinter",
        "IPython",
        "matplotlib",
        "scipy",
        "pandas",
        "torch",
        "torchvision",
        "torchaudio",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="hawkoffer-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
