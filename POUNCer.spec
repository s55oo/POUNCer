# -*- mode: python ; coding: utf-8 -*-
# Build with:  python -m PyInstaller --noconfirm POUNCer.spec
# (build.bat and the GitHub Actions workflow both use this file - don't pass
# command-line options to PyInstaller, they would regenerate it).
#
# Antivirus false positives (Trojan:Win32/Wacatac.B!ml): see ARCHITECTURE.md
# "Build & run". upx stays False - UPX packing is a common AV trigger.

import sys

# the Sun Valley ttk theme (dark / light looks): Tcl files + sprites
datas = [('pouncer/sunvalley', 'pouncer/sunvalley')]
runtime_hooks = []
if sys.platform == 'darwin':
    import certifi
    datas += [(certifi.where(), 'certificates')]
    runtime_hooks = ['packaging/macos_certificates.py']

a = Analysis(
    ['pouncer_main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='POUNCer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
