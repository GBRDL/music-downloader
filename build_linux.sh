#!/usr/bin/env bash
# Builds dist/MusicDownloader.AppImage (x86_64 Linux). Run on Linux, or inside WSL on Windows.
#   needs: python3 with tkinter and venv (Ubuntu/Debian: sudo apt install python3 python3-venv python3-tk curl)
#   optional: PYTHON=/path/to/python3  to choose the interpreter
# The build happens in a temporary copy on the Linux filesystem (WSL's /mnt/c is too slow and breaks venvs);
# only the finished AppImage is copied back into ./dist.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
APP=MusicDownloader

command -v "$PY" >/dev/null || { echo "python3 not found (sudo apt install python3 python3-venv python3-tk curl)"; exit 1; }
"$PY" -c "import tkinter" 2>/dev/null || { echo "Python has no tkinter. Run: sudo apt install python3-tk"; exit 1; }
"$PY" -c "import venv, ensurepip" 2>/dev/null || { echo "Python venv is missing. Run: sudo apt install python3-venv"; exit 1; }
command -v curl >/dev/null || { echo "curl not found. Run: sudo apt install curl"; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/mdbuild.XXXXXX")"
trap 'rm -rf "$W"' EXIT
cp "$SRC/app.py" "$SRC/playlist_to_artists_mp3.py" "$SRC/spotify_to_mp3.py" "$SRC/make_icon.py" "$W/"
cd "$W"

echo "[1/6] Python environment..."
"$PY" -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet pyinstaller customtkinter "yt-dlp[default]" psutil requests mutagen pillow imageio-ffmpeg

echo "[2/6] Icon..."
python make_icon.py app.ico >/dev/null
python - <<PY
from PIL import Image
im = Image.open("app.ico"); sizes = im.info.get("sizes") or {im.size}
im.size = max(sizes); im.load(); im.convert("RGBA").resize((256, 256)).save("$APP.png")
PY

echo "[3/6] ffmpeg and deno..."
cp "$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" ffmpeg
curl -fsSL -o deno.zip "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip"
python -c "import zipfile; zipfile.ZipFile('deno.zip').extract('deno', '.')"
chmod +x ffmpeg deno

echo "[4/6] Packaging the app (a few minutes)..."
EJS=""; python -c "import yt_dlp_ejs" 2>/dev/null && EJS="--collect-all yt_dlp_ejs"
pyinstaller --noconfirm --clean --name "$APP" --distpath pyi --workpath pyi_work --specpath . \
  --collect-all customtkinter $EJS \
  --add-binary "$PWD/ffmpeg:." --add-binary "$PWD/deno:." --add-data "$PWD/app.ico:." \
  app.py >/dev/null

echo "[5/6] Building the AppImage..."
mkdir -p AppDir/usr/lib
cp -r "pyi/$APP" "AppDir/usr/lib/$APP"
cp "$APP.png" "AppDir/$APP.png"
cat > "AppDir/$APP.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Music Downloader
Exec=$APP
Icon=$APP
Categories=AudioVideo;Audio;
Terminal=false
DESK
cat > AppDir/AppRun <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/MusicDownloader/MusicDownloader" "$@"
RUN
chmod +x AppDir/AppRun
curl -fsSL -o appimagetool "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x appimagetool
# --appimage-extract-and-run: works even where FUSE is missing (WSL, containers)
ARCH=x86_64 ./appimagetool --appimage-extract-and-run --no-appstream AppDir "$APP.AppImage" >/dev/null
chmod +x "$APP.AppImage"

echo "[6/6] Copying to dist..."
mkdir -p "$SRC/dist/${APP}_AppImage"
cp "$APP.AppImage" "$SRC/dist/${APP}_AppImage/$APP.AppImage"
ls -lh "$SRC/dist/${APP}_AppImage/$APP.AppImage"
