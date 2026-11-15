#!/usr/bin/env python3
"""Builds dist\\MusicDownloader_exe\\MusicDownloader.exe (Windows) and dist\\MusicDownloader_AppImage\\MusicDownloader.AppImage (Linux, via WSL).
Run via build_exe.bat.  Use --no-linux to build only the Windows exe."""
import subprocess, sys, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
DIST = ROOT / "dist"
EXE_DIR = DIST / "MusicDownloader_exe"            # send this folder's file to Windows users
APPIMAGE_DIR = DIST / "MusicDownloader_AppImage"  # send this folder's file to Linux users
EXE = EXE_DIR / "MusicDownloader.exe"
APPIMAGE = APPIMAGE_DIR / "MusicDownloader.AppImage"
PERSONAL_NAMES = {"settings.json", "state.json", "jobs.json", "failed_items.txt"}


def find_personal(folder: Path):
    """Anything that must never be shipped: saved settings, progress, failure logs, cookies, CSVs."""
    hits = []
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        n = p.name.lower()
        if n in PERSONAL_NAMES or "cookies" in n or n.endswith(".csv"):
            hits.append(p)
    return hits


def run(*cmd, **kw):
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True, **kw)


def wsl_distros():
    """Names of installed WSL distros ([] when WSL is missing)."""
    try:
        out = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=30).stdout
    except Exception:
        return []
    text = out.decode("utf-16-le", errors="ignore") if b"\x00" in out else out.decode("utf-8", errors="ignore")
    return [l.strip().strip("\x00") for l in text.splitlines() if l.strip() and not l.strip().lower().startswith("docker")]


def build_linux():
    distros = wsl_distros()
    if not distros:
        print("  Skipped: WSL (Windows Subsystem for Linux) is not installed.")
        print("  To get the Linux build, open PowerShell as admin once and run:  wsl --install -d Ubuntu")
        print("  then restart, finish the Ubuntu first-run setup, run  sudo apt update && sudo apt install -y python3 python3-venv python3-tk curl")
        print("  inside Ubuntu, and run build_exe.bat again. (Or copy this folder to any Linux PC and run ./build_linux.sh)")
        return False
    path = subprocess.run(["wsl.exe", "wslpath", "-a", str(ROOT)], capture_output=True, text=True).stdout.strip()
    if not path:
        print("  Skipped: could not translate the folder path for WSL.")
        return False
    print(f"  Using WSL ({distros[0]}). The first run downloads tools and takes several minutes...")
    r = subprocess.run(["wsl.exe", "bash", f"{path}/build_linux.sh"], cwd=ROOT)
    if r.returncode != 0 or not APPIMAGE.exists():
        print("  The Linux build failed (see the messages above). If it says something is missing, run in Ubuntu:")
        print("    sudo apt update && sudo apt install -y python3 python3-venv python3-tk curl")
        return False
    return True


def main():
    skip_linux = "--no-linux" in sys.argv
    if not VENV_PY.exists():
        sys.exit(f"Could not find {VENV_PY}")
    if Path(sys.executable).resolve() != VENV_PY.resolve():
        # Always build with the project's virtual environment.
        sys.exit(subprocess.call([str(VENV_PY), str(Path(__file__).resolve())] + sys.argv[1:]))

    running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MusicDownloader.exe"], capture_output=True, text=True).stdout
    if "MusicDownloader.exe" in running:
        sys.exit("MusicDownloader.exe is still running. Close it and run this again.")

    print("[1/5] Creating icon...")
    run(VENV_PY, "make_icon.py", "app.ico")

    print("[2/5] Checking PyInstaller...")
    if subprocess.run([str(VENV_PY), "-m", "PyInstaller", "--version"], cwd=ROOT, capture_output=True).returncode != 0:
        run(VENV_PY, "-m", "pip", "install", "pyinstaller")

    print("[3/5] Building the Windows exe (a few minutes)...")
    old_folder = DIST / "MusicDownloader"
    if old_folder.is_dir():
        print("  Removing the old dist\\MusicDownloader folder (replaced by the single exe).")
        shutil.rmtree(old_folder, ignore_errors=True)
    (DIST / "MusicDownloader.exe").unlink(missing_ok=True)   # old flat layout
    EXE_DIR.mkdir(parents=True, exist_ok=True)
    EXE.unlink(missing_ok=True)
    run(VENV_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", EXE_DIR, "MusicDownloader.spec")
    if not EXE.exists():
        sys.exit("Build finished but MusicDownloader.exe is missing.")

    linux_ok = False
    if skip_linux:
        print("[4/5] Linux build skipped (--no-linux).")
    else:
        print("[4/5] Building the Linux AppImage...")
        linux_ok = build_linux()

    print("[5/5] Checking that nothing personal is in dist...")
    hits = find_personal(DIST)
    if hits:
        for h in hits:
            print("  FOUND", h)
        sys.exit("Personal files found above. Delete them before sending.")

    print(f"\nDone. In {DIST}:")
    print("  MusicDownloader_exe\\MusicDownloader.exe               (Windows)")
    print("  MusicDownloader_AppImage\\MusicDownloader.AppImage     (Linux)" if linux_ok or APPIMAGE.exists() else "  (no Linux AppImage this time, see above)")
    print("Send those files. Do NOT run the exe inside dist first (it creates settings.json beside it); copy it elsewhere to test.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed (exit code {e.returncode}).")
        input("Press Enter to close...")
        sys.exit(1)
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"\n{e.code}")
            input("Press Enter to close...")
        raise
    input("\nPress Enter to close...")
