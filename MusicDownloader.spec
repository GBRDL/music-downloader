# -*- mode: python ; coding: utf-8 -*-
# One-file Windows build: dist\MusicDownloader.exe
from PyInstaller.utils.hooks import collect_all

datas = [('app.ico', '.')]
binaries = [('ffmpeg.exe', '.'), ('deno.exe', '.')]
hiddenimports = []
for pkg in ('customtkinter', 'yt_dlp_ejs'):   # yt_dlp_ejs: YouTube JS challenge solver (skipped if not installed)
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name='MusicDownloader',
    icon='app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
