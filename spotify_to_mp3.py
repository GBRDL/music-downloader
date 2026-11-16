#!/usr/bin/env python3
from __future__ import annotations
import argparse, logging, os, re, shutil, sys, time, subprocess, threading
from dataclasses import dataclass
from pathlib import Path
from yt_dlp import YoutubeDL

def _hide_child_consoles(flag=0x08000000):
    """Windows only: start every helper program (ffmpeg, deno) with CREATE_NO_WINDOW so no terminal ever opens.
    Patched globally because yt-dlp launches ffmpeg/deno itself, and on Windows 11 each one can otherwise show up
    as a terminal window."""
    orig = subprocess.Popen.__init__
    def quiet_init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | flag
        orig(self, *args, **kwargs)
    subprocess.Popen.__init__ = quiet_init

if sys.platform == "win32":
    _hide_child_consoles()

if getattr(sys, 'frozen', False):
    INTERNAL_DIR = Path(sys._MEIPASS)
    APP_DIR = Path(sys.executable).parent
    os.environ["PATH"] = str(INTERNAL_DIR) + os.pathsep + os.environ.get("PATH", "")   # bundled ffmpeg / deno
else:
    APP_DIR = Path(__file__).parent
    INTERNAL_DIR = APP_DIR

def _data_dir() -> Path:
    """One dedicated folder for settings, progress and caches, so nothing is scattered next to the program:
    Windows %APPDATA%\\MusicDownloader, Linux ~/.local/share/MusicDownloader, macOS ~/Library/Application Support."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "MusicDownloader"

BASE_DIR = _data_dir()
BASE_DIR.mkdir(parents=True, exist_ok=True)

def _migrate_old_files():
    """Older versions kept these next to the program. Move them over once so settings and progress carry on."""
    for name in ("settings.json", "state.json", "jobs.json", "failed_items.txt"):
        old, new = APP_DIR / name, BASE_DIR / name
        try:
            if old.is_file() and not new.exists(): shutil.move(str(old), str(new))
        except Exception: pass

_migrate_old_files()

def ffmpeg_path() -> str:
    """The bundled ffmpeg when running as a packaged app, otherwise whatever is on PATH."""
    if getattr(sys, 'frozen', False):
        exe = INTERNAL_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if exe.exists(): return str(exe)
    return "ffmpeg"

SEARCH_RESULTS = 5
fail_log = logging.getLogger("failures")

PROGRESS_CALLBACK = None
THROTTLE_CALLBACK = None 
STOP_CHECK_CALLBACK = None

class StopDownload(Exception):
    pass

_FINAL_LOCKS_GUARD = threading.Lock()
_FINAL_LOCKS: dict[str, threading.Lock] = {}

def _final_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _FINAL_LOCKS_GUARD:
        lock = _FINAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FINAL_LOCKS[key] = lock
        return lock

def progress_hook(d, thread_name="dl"):
    if STOP_CHECK_CALLBACK and STOP_CHECK_CALLBACK():
        raise StopDownload("Stopped by user")
    if PROGRESS_CALLBACK:
        PROGRESS_CALLBACK(d, thread_name)

@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    album: str
    duration_s: float
    url: str

class Missing(Exception): pass

_REPLACE = str.maketrans({":": " -", "/": "-", "\\": "-", "|": "-", '"': "'", "?": "", "*": "", "<": "", ">": ""})
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

_VERSION_TAG = re.compile(r"\s*[-(\[]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?|deluxe|bonus track|mono|stereo)\b.*$", re.I)

def safe_name(text: str, max_len: int = 120) -> str:
    name = "".join(ch for ch in text.translate(_REPLACE) if ch >= " ")
    name = re.sub(r"\s+", " ", name).strip(" .")[:max_len].rstrip(" .")
    if not name: name = "untitled"
    if name.split(".")[0].upper() in _RESERVED: name = f"_{name}"
    return name

def dedupe_key(title: str) -> str:
    return re.sub(r"\s+", " ", _VERSION_TAG.sub("", title)).strip().casefold()

def one_line(exc: Exception) -> str:
    msg = " ".join(re.sub(r"\x1b?\[[0-9;]*m", "", str(exc)).split())
    return msg if isinstance(exc, Missing) else f"{type(exc).__name__}: {msg}"

def record(kind: str, subject: str, exc: Exception, link: str = "") -> None:
    fail_log.warning("%s | %s | %s%s", kind, subject, one_line(exc), f" | {link}" if link else "")

def ydl_options(args: argparse.Namespace, thread_name="dl", **extra) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "progress_hooks": [lambda d: progress_hook(d, thread_name)],
        "socket_timeout": 15, "retries": 2, "fragment_retries": 2,
        "concurrent_fragment_downloads": 2, **extra
    }
    cookie_path = getattr(args, 'cookies_from_browser', None)
    if cookie_path:
        cp = Path(cookie_path)
        if cp.exists() and cp.stat().st_size > 50:
            try:
                first_line = cp.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
                if "Netscape" in first_line or "# HTTP Cookie File" in first_line or "youtube.com" in first_line:
                    thread_cookie = BASE_DIR / f".cache/cookies_{thread_name}.txt"
                    thread_cookie.parent.mkdir(exist_ok=True)
                    shutil.copy(cp, thread_cookie)
                    opts["cookiefile"] = str(thread_cookie)
            except Exception: pass
    return opts

def find_audio_source(searcher: YoutubeDL, track: Track, tolerance: float, primary_source: str, max_duration: int) -> str:
    query = f"{track.artist} - {track.title}"
    sources = [("youtube", f"ytsearch{SEARCH_RESULTS}:{query}"), ("soundcloud", f"scsearch{SEARCH_RESULTS}:{query}")]
    if primary_source == "soundcloud": sources.reverse()
    last_error = None

    for source_name, search_query in sources:
        try:
            if STOP_CHECK_CALLBACK and STOP_CHECK_CALLBACK(): raise StopDownload()
            info = searcher.extract_info(search_query, download=False)
            entries = [e for e in (info or {}).get("entries") or [] if e and (e.get("duration") or 0) <= max_duration]
            if not entries: continue
            if tolerance <= 0 or track.duration_s <= 0:
                return entries[0].get("url") or entries[0].get("webpage_url")
            scored = [(abs(e["duration"] - track.duration_s), e) for e in entries if e.get("duration")]
            scored = [pair for pair in scored if pair[0] <= tolerance]
            if scored:
                best = min(scored, key=lambda pair: pair[0])[1]
                return best.get("url") or best.get("webpage_url")
        except StopDownload: raise
        except Exception as e: last_error = e
    
    if last_error: raise Missing(f"Search failed: {one_line(last_error)}")
    raise Missing(f"No suitable result found for {track.title}")

def download_raw(url: str, temp_dir: Path, artist: str, title: str, args: argparse.Namespace, thread_name: str, job_id: str = "0") -> Path:
    if STOP_CHECK_CALLBACK and STOP_CHECK_CALLBACK(): raise StopDownload()
    safe_job = "".join(ch for ch in str(job_id) if ch.isalnum() or ch in ("-", "_")) or "0"
    esc = lambda s: s.replace("%", "%%")
    # Temp file is named by job id only: long or odd titles can't blow Windows' 260-character path limit.
    outtmpl = esc(str(temp_dir / safe_job)) + ".%(ext)s"
    
    ffmpeg_exe = ffmpeg_path()
    opts = ydl_options(args, thread_name=thread_name, format="bestaudio/best", outtmpl=outtmpl, noplaylist=True, ffmpeg_location=ffmpeg_exe)
    
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))

def source_audio_codec(path: Path) -> str:
    """Audio codec of a downloaded file ('opus', 'aac', ...) as ffmpeg reports it; '' if it can't be read."""
    try:
        r = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, errors="replace", timeout=30)
        m = re.search(r"Audio:\s*([A-Za-z0-9_]+)", r.stderr or "")
        return m.group(1).lower() if m else ""
    except Exception:
        return ""

def convert_audio(raw_path: Path, target_stem: Path, fmt: str, quality: str, part_id: str = "0", stop_check=None) -> Path:
    ext = "m4a" if fmt == "m4a" else ("opus" if fmt == "opus" else "mp3")
    final_path = Path(f"{target_stem}.{ext}")
    safe_part = "".join(ch for ch in str(part_id) if ch.isalnum() or ch in ("-", "_")) or "0"
    temp_path = Path(f"{target_stem}.{safe_part}.part.{ext}")

    final_path.parent.mkdir(parents=True, exist_ok=True)

    if ext == "opus": codec, bitrate = "libopus", "128k"
    elif ext == "m4a": codec, bitrate = "aac", f"{quality}k"
    else: codec, bitrate = "libmp3lame", f"{quality}k"

    ffmpeg_exe = ffmpeg_path()
    # YouTube's best audio is usually Opus (webm) or AAC (m4a). When that already matches the wanted format, just
    # re-wrap it: no quality loss, almost no CPU. Anything else is re-encoded as before.
    src = source_audio_codec(raw_path)
    if (ext == "opus" and src == "opus") or (ext == "m4a" and src == "aac"):
        audio_args = ["-c:a", "copy"]
    else:
        audio_args = ["-c:a", codec, "-b:a", bitrate, "-threads", "2"]
    cmd = [ffmpeg_exe, "-y", "-i", str(raw_path), "-vn", *audio_args, str(temp_path)]

    # CREATE_NO_WINDOW: the packaged Windows app has no console, so without it every ffmpeg run pops up its own
    # terminal window (and closing that window kills ffmpeg with exit code 3221225786).
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 600
    try:
        while proc.poll() is None:
            if stop_check and stop_check():
                proc.kill()
                proc.wait(timeout=5)
                temp_path.unlink(missing_ok=True)
                raise StopDownload("Stopped by user")
            if time.monotonic() >= deadline:
                proc.kill()
                proc.wait(timeout=5)
                temp_path.unlink(missing_ok=True)
                raise TimeoutError("ffmpeg timed out")
            time.sleep(0.2)
        if proc.returncode != 0:
            temp_path.unlink(missing_ok=True)
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        with _final_lock(final_path):
            os.replace(temp_path, final_path)
        raw_path.unlink(missing_ok=True)
        return final_path
    except Exception:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        temp_path.unlink(missing_ok=True)
        raise

def tag_mp3(path: Path, track: Track) -> None:
    try:
        if path.suffix == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError
            try: tags = EasyID3(str(path))
            except ID3NoHeaderError: tags = EasyID3()
            tags["title"], tags["artist"], tags["album"] = [track.title], [track.artist], [track.album]
            tags.save(str(path))
        elif path.suffix == ".m4a":
            from mutagen.mp4 import MP4
            tags = MP4(str(path))
            tags["\xa9nam"], tags["\xa9ART"], tags["\xa9alb"] = track.title, track.artist, track.album
            tags.save()
        elif path.suffix == ".opus":
            from mutagen.oggopus import OggOpus
            tags = OggOpus(str(path))
            tags["title"], tags["artist"], tags["album"] = [track.title], [track.artist], [track.album]
            tags.save()
    except ImportError: pass
    except Exception: pass
