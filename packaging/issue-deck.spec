# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for a local IssueDeck executable.

Build (from the repo root, in your venv with pyinstaller installed):

    pip install pyinstaller
    pyinstaller packaging/issue-deck.spec

Produces a onedir bundle in ``dist/JiraPuller/`` (run the ``JiraPuller``
executable inside it). ``build/`` and ``dist/`` are gitignored — do NOT commit
the generated binaries; only this spec is committed.

Notes:
* onedir (not onefile) is used for faster startup and simpler Qt plugin bundling.
* ``keyring`` backends are imported lazily at runtime, so they are collected as
  hidden imports; the app still runs (plaintext-fallback token storage) if the
  extra is absent at build time.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

try:
    hiddenimports = collect_submodules("keyring")
except Exception:  # keyring extra not installed at build time
    hiddenimports = []

entry_script = os.path.join(SPECPATH, "..", "issue-deck.py")  # noqa: F821 (SPECPATH is injected)

a = Analysis(
    [entry_script],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JiraPuller",
    console=False,          # GUI app: no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JiraPuller",
)
