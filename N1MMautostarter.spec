# -*- mode: python ; coding: utf-8 -*-
# N1MM Autostarter - POUNCer's autostart as a program of its own (Windows).
# Build with:  python -m PyInstaller --noconfirm N1MMautostarter.spec
# Same build as POUNCer.exe (bootloader compiled from source, no UPX).

a = Analysis(
    ['autostarter_main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pouncer.app', 'pouncer.web', 'pouncer.rbn', 'pouncer.runhist'],
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
    name='N1MMautostarter',
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
