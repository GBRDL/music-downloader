#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, hashlib, json, logging, os, re, socket, sys, time, urllib.parse, queue, shutil
from pathlib import Path
import psutil
import requests
from yt_dlp import YoutubeDL
import spotify_to_mp3 as core
import threading

BASE_DIR = core.BASE_DIR   # the per-user data folder (settings, progress, caches)

MB = "https://musicbrainz.org/ws/2"
# MusicBrainz asks for a real contact (an e-mail or a project URL) and drops connections from anonymous-looking clients.
HEADERS = {"User-Agent": "MusicDownloader/1.0 ( https://github.com/GBRDL/music-downloader )"}
_last_call = 0.0
CPU_PROFILE = "smart"
LOG_CB = None
STOP_EVENT = threading.Event()
JOBS_FILE = BASE_DIR / "jobs.json"
STATE_FILE = BASE_DIR / "state.json"
STATE_LOCK = threading.Lock()
GLOBAL_CPU = 0.0
_THROTTLE_LOCK = threading.Lock()
_last_throttle = 0.0
_last_throttle_log = 0.0
_BATCH_DIGEST = ""  # identifies the job list that state.json progress belongs to

class RateGate:
    """Spaces the START of downloads at least `gap` seconds apart across ALL workers, so three workers can't
    triple the request rate. (Before, each worker waited on its own, which is how you get soft-banned.)"""
    def __init__(self):
        self._lock = threading.Lock(); self._next = 0.0
    def reset(self):
        with self._lock: self._next = 0.0
    def wait(self, gap: float) -> bool:
        """Block until it's this caller's turn. True means stop was requested."""
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + max(0.0, gap)
        return pause_or_stop(start - now)

DOWNLOAD_GATE = RateGate()

def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

def pause_or_stop(seconds: float) -> bool:
    """Sleep until the timeout elapses. True means stop was requested."""
    if seconds <= 0:
        return STOP_EVENT.is_set()
    return STOP_EVENT.wait(seconds)

def check_hardware_throttle():
    global _last_throttle, _last_throttle_log
    if STOP_EVENT.is_set():
        raise core.StopDownload("Stopped by user")
    if CPU_PROFILE == "max":
        return

    now = time.monotonic()
    with _THROTTLE_LOCK:
        if now - _last_throttle < 2.0:
            return
        cpu = GLOBAL_CPU
        if CPU_PROFILE == "smart" and cpu > 65.0:
            pause, message = 2.0, f"System busy ({cpu:.0f}%). Pausing..."
        elif CPU_PROFILE == "low" and cpu > 30.0:
            pause, message = 1.0, f"Low Impact mode ({cpu:.0f}%). Slowing down..."
        else:
            return
        _last_throttle = now
        log_it = now - _last_throttle_log > 30.0   # the same line every 2 seconds just buries the real messages
        if log_it: _last_throttle_log = now
    if LOG_CB and log_it:
        LOG_CB(message)
    if pause_or_stop(pause):
        raise core.StopDownload("Stopped by user")

def is_stopped(): return STOP_EVENT.is_set()

_NET_ERROR_MARKERS = ("getaddrinfo failed", "failed to resolve", "name or service not known",
                      "temporary failure in name resolution", "network is unreachable",
                      "no route to host", "nodename nor servname")

def is_network_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _NET_ERROR_MARKERS)

def internet_up() -> bool:
    """True if DNS works, i.e. we are online. (An offline machine can't resolve any hostname.)"""
    try:
        socket.getaddrinfo("www.youtube.com", 443)
        return True
    except OSError:
        return False

_MB_SESSION = requests.Session()   # reuses one connection; MusicBrainz drops rapid fresh TLS connections

def mb_get(path: str, **params) -> dict:
    global _last_call, _MB_SESSION
    for attempt in range(6):
        wait = 1.1 - (time.time() - _last_call)
        if wait > 0 and pause_or_stop(wait):
            raise core.StopDownload("Stopped by user")
        try:
            r = _MB_SESSION.get(f"{MB}/{path}", params={**params, "fmt": "json"}, headers=HEADERS, timeout=30)
        except requests.RequestException:
            # Dropped connection (e.g. SSL EOF): throw the connection away, wait for internet, back off, retry.
            try: _MB_SESSION.close()
            except Exception: pass
            _MB_SESSION = requests.Session()
            if attempt == 5: raise
            while not internet_up():
                if pause_or_stop(5): raise core.StopDownload("Stopped by user")
            if pause_or_stop(min(5 * 2 ** attempt, 40)):
                raise core.StopDownload("Stopped by user")
            continue
        _last_call = time.time()
        if r.status_code in (429, 503):
            if pause_or_stop(5 * (attempt + 1)):
                raise core.StopDownload("Stopped by user")
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("MusicBrainz rate limit")

def resolve_artist(name: str) -> dict:
    q = re.sub(r'(["\\])', r"\\\1", name)
    hits = mb_get("artist", query=f'artist:"{q}"', limit=5).get("artists", [])
    if not hits: raise core.Missing(f"no match {name!r}")
    exact = [a for a in hits if a["name"].casefold() == name.casefold()]
    return (exact or hits)[0]

def fetch_deezer_top_10(artist_name: str, max_duration: int, log_cb) -> list[core.Track]:
    try:
        res = requests.get(f"https://api.deezer.com/search/artist?q={urllib.parse.quote(artist_name)}&limit=1", timeout=10).json()
        if not res.get("data"): return []
        top_res = requests.get(f"https://api.deezer.com/artist/{res['data'][0]['id']}/top?limit=10", timeout=10).json()
        tracks = []
        for item in top_res.get("data", []):
            if item.get("duration", 0) <= max_duration:
                tracks.append(core.Track(res["data"][0]["name"], item["title"], "", item["duration"], item.get("link", "")))
        log_cb(f"Fetched Top {len(tracks)} hits for {res['data'][0]['name']} from Deezer.")
        return tracks
    except Exception as e:
        log_cb(f"Deezer failed: {e}"); return []

def fetch_catalog(artist: dict, limit: int, searcher: YoutubeDL, log_cb, max_duration: int, skip_regex: re.Pattern) -> list[core.Track]:
    cache_dir = BASE_DIR / ".cache"; cache_dir.mkdir(exist_ok=True)
    safe_id = "".join(c for c in artist.get("id", "unknown") if c.isalnum() or c in ("-", "_"))
    cache_file = cache_dir / f"{safe_id}.json"
    
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if (
                isinstance(data, dict)
                and data.get("max_duration") == max_duration
                and data.get("limit") == limit
                and data.get("skip") == skip_regex.pattern
            ):
                return [core.Track(**t) for t in data.get("tracks", [])]
        except Exception: pass

    log_cb(f"Fetching catalog for {artist['name']}...")
    tracks, seen = [], set()
    offset, release_groups = 0, []

    while True:
        data = mb_get("release-group", artist=artist["id"], limit=100, offset=offset)
        groups = data.get("release-groups", [])
        for g in groups:
            primary = (g.get("primary-type") or "").lower()
            secondary = [s.lower() for s in (g.get("secondary-types") or [])]
            if primary in ("album", "ep") and not any(s in secondary for s in ["live", "remix", "compilation", "soundtrack", "karaoke", "spokenword", "interview", "dj-mix"]):
                release_groups.append(g)
        offset += len(groups)
        if not groups or offset >= data.get("release-group-count", 0): break

    for rg in release_groups:
        if limit and len(tracks) >= limit: break
        rel_data = mb_get("release", **{"release-group": rg["id"]}, limit=1)
        releases = rel_data.get("releases", [])
        if not releases: continue
        
        rel_detail = mb_get(f"release/{releases[0]['id']}", inc="recordings")
        for medium in rel_detail.get("media", []):
            for track in medium.get("tracks", []):
                if limit and len(tracks) >= limit: break
                rec = track.get("recording", {})
                title = rec.get("title") or track.get("title")
                duration_s = (rec.get("length") or track.get("length") or 0) / 1000
                if not title or duration_s > max_duration or skip_regex.search(title) or skip_regex.search(rec.get("disambiguation") or ""): continue
                key = core.dedupe_key(title)
                if key in seen: continue
                seen.add(key)
                tracks.append(core.Track(artist["name"], title, releases[0].get("title") or "", duration_s, f"https://musicbrainz.org/recording/{rec.get('id', '')}"))

    payload = {
        "max_duration": max_duration,
        "limit": limit,
        "skip": skip_regex.pattern,
        "tracks": [t.__dict__ for t in tracks],
    }
    try: atomic_write_text(cache_file, json.dumps(payload, ensure_ascii=False))
    except Exception: pass
    return tracks[:limit] if limit else tracks

# ------------------------------------------------------------------ YouTube as a source
_URL_COLS = {"url", "link", "video url", "video link", "youtube url", "youtube link", "webpage url", "webpage_url", "video_url"}
_ID_COLS = ("video id", "video_id", "videoid", "id")
_TITLE_COLS = ("title", "video title", "track name", "track", "song", "name")
_ARTIST_COLS = ("artist", "artist name(s)", "artist name", "artists", "channel", "channel name", "uploader", "author")
_DUR_MS_COLS = ("track duration (ms)", "duration (ms)")
_VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")
_TAIL_TAGS = re.compile(r"\s*[\(\[]\s*(?:official\s*)?(?:music\s*)?(?:hd\s*)?(?:video|audio|lyrics?(?:\s*video)?|visuali[sz]er|mv|hq|4k)[^\)\]]*[\)\]]\s*$", re.I)
_ANSI = re.compile(r"\x1b?\[[0-9;]*m")

def clean_msg(exc) -> str:
    return " ".join(_ANSI.sub("", str(exc)).split())

def looks_like_url(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith(("http://", "https://", "www.", "youtube.com", "youtu.be", "music.youtube.com", "m.youtube.com"))

def normalize_url(text: str) -> str:
    t = (text or "").strip().strip('"').strip("'")
    return t if t.lower().startswith(("http://", "https://")) else "https://" + t

def youtube_artist_title(title: str, uploader: str):
    """Best-effort split of a video title into (artist, song). Falls back to the channel name."""
    uploader = re.sub(r"\s*-\s*Topic\s*$", "", uploader or "", flags=re.I).strip() or "Unknown Artist"
    title = (title or "").strip()
    artist, song = uploader, title
    for sep in (" - ", " – ", " — "):
        if sep in title:
            a, s = (p.strip() for p in title.split(sep, 1))
            if a and s:
                artist, song = a, s
                break
    cleaned = _TAIL_TAGS.sub("", song).strip()
    return artist, (cleaned or song)

def oembed_info(url: str):
    """Title and channel of one video via YouTube's lightweight oEmbed endpoint (no player API, no cookies)."""
    r = requests.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, headers=HEADERS, timeout=10)
    r.raise_for_status()
    d = r.json()
    return d.get("title") or "", d.get("author_name") or ""

def _flat_entries(url: str, args):
    """List the videos behind a link. Returns (playlist_title, [entry dicts])."""
    with YoutubeDL(core.ydl_options(args, thread_name="PL", extract_flat="in_playlist")) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise core.Missing("YouTube returned nothing for that link")
    if info.get("entries") is None:
        return "", [info]
    return info.get("title") or "", [e for e in info["entries"]]

def _entry_url(e: dict) -> str:
    u = str(e.get("url") or "")
    return u if u.startswith("http") else f"https://www.youtube.com/watch?v={e.get('id')}"

def _jobs_from_youtube_link(url: str, args, max_duration: int, log_cb) -> list:
    album, entries = _flat_entries(url, args)
    jobs, seen, too_long, unavailable = [], set(), 0, 0
    for e in entries:
        if STOP_EVENT.is_set(): raise core.StopDownload("Stopped by user")
        title = ((e or {}).get("title") or "").strip()
        vid = (e or {}).get("id") or ""
        if not e or not vid or not title or title in ("[Private video]", "[Deleted video]") or e.get("ie_key") not in (None, "Youtube"):
            unavailable += 1
            continue
        if vid in seen: continue
        seen.add(vid)
        dur = float(e.get("duration") or 0)
        if max_duration and dur and dur > max_duration:
            too_long += 1
            continue
        artist, song = youtube_artist_title(title, e.get("channel") or e.get("uploader") or "")
        jobs.append((artist, core.Track(artist, song, album, dur, _entry_url(e))))
    log_cb(f"YouTube playlist: {len(jobs)} videos queued.")
    if unavailable: log_cb(f"Skipped {unavailable} private/deleted/unavailable entries (private playlists need your cookies).")
    if too_long: log_cb(f"Skipped {too_long} videos longer than your Max Length setting.")
    return jobs

def _jobs_from_youtube_csv(path: Path, args, max_duration: int, log_cb, update_ui_cb) -> list:
    if not path.is_file():
        raise core.Missing(f"File not found: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and looks_like_url(lines[0].split(",")[0]):
        rows = [{"url": ln.split(",")[0].strip()} for ln in lines]          # plain list of links, no header
    else:
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(lines)]
    jobs, seen = [], set()
    for n, row in enumerate(rows, 1):
        if STOP_EVENT.is_set(): raise core.StopDownload("Stopped by user")
        url = next((row[c] for c in row if c in _URL_COLS and looks_like_url(row[c])), "")
        if url: url = normalize_url(url)
        m = _VIDEO_ID.search(url) if url else None
        vid = m.group(1) if m else ""
        if url and not vid and "list=" in url:                              # a playlist link inside the file
            jobs.extend(_jobs_from_youtube_link(url, args, max_duration, log_cb))
            continue
        if not url:
            vid = next((row[c] for c in _ID_COLS if re.fullmatch(r"[\w-]{11}", row.get(c, ""))), "")
            if vid: url = f"https://www.youtube.com/watch?v={vid}"
        title = next((row[c] for c in _TITLE_COLS if row.get(c)), "")
        artist = next((row[c] for c in _ARTIST_COLS if row.get(c)), "").split(";")[0].strip()
        dur = 0.0
        for c in _DUR_MS_COLS:
            try: dur = float(row.get(c) or 0) / 1000; break
            except ValueError: pass
        if not title and not url:
            continue
        if url and not title:
            update_ui_cb(0, 0, f"Reading list ({n}/{len(rows)})...")
            if pause_or_stop(0.4): raise core.StopDownload("Stopped by user")
            try:
                t, a = oembed_info(url)
                artist, title = youtube_artist_title(t, a)
            except Exception:
                artist, title = "Unknown Artist", f"video {vid or n}"
        elif title and not artist:
            author = ""
            if url:
                if pause_or_stop(0.4): raise core.StopDownload("Stopped by user")
                try: author = oembed_info(url)[1]
                except Exception: pass
            artist, title = youtube_artist_title(title, author)
        key = vid or f"{artist}|{title}".casefold()
        if key in seen: continue
        seen.add(key)
        jobs.append((artist, core.Track(artist or "Unknown Artist", title, "", dur, url)))
    log_cb(f"File: {len(jobs)} tracks queued.")
    return jobs

def build_youtube_jobs(source: str, args, max_duration: int, log_cb, update_ui_cb) -> list:
    """A YouTube playlist/video link, or a CSV/text file, -> [(artist, Track)]. Tracks with a link download directly."""
    source = (source or "").strip().strip('"')
    if looks_like_url(source):
        return _jobs_from_youtube_link(normalize_url(source), args, max_duration, log_cb)
    return _jobs_from_youtube_csv(Path(source), args, max_duration, log_cb, update_ui_cb)

# ------------------------------------------------------------------ Quick converter
CONVERT_STOP = threading.Event()

def _expand_link(link: str, args, log_cb) -> list:
    """One pasted link -> video URLs. A video link stays one; a playlist link expands."""
    url = normalize_url(link)
    if _VIDEO_ID.search(url) and ("list=" not in url or "v=" in url or "youtu.be/" in url):
        return [url]
    _, entries = _flat_entries(url, args)
    out = [_entry_url(e) for e in entries if e and e.get("id") and e.get("title") not in ("[Private video]", "[Deleted video]") and e.get("ie_key") in (None, "Youtube")]
    if not out: log_cb(f"No videos found in {link}")
    return out

def quick_convert(urls, out_dir: str, fmt: str, quality: str, cookie_path: str, delay: float, log_cb, status_cb, bar_cb):
    """Download each link (every video, for a playlist link) and convert to mp3/m4a/opus. Returns (ok, failed, folder)."""
    class Args: pass
    args = Args(); args.cookies_from_browser = cookie_path
    ext = "m4a" if fmt == "m4a" else ("opus" if fmt == "opus" else "mp3")
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    temp = out / ".temp_converter"; temp.mkdir(parents=True, exist_ok=True)
    prev_stop, prev_prog = core.STOP_CHECK_CALLBACK, core.PROGRESS_CALLBACK
    core.STOP_CHECK_CALLBACK = CONVERT_STOP.is_set
    def hook(d, _name):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total and d.get("downloaded_bytes"): bar_cb(d["downloaded_bytes"] / total)
    core.PROGRESS_CALLBACK = hook
    ffmpeg_exe = core.ffmpeg_path()
    ok = failed = 0
    try:
        items = []
        for link in urls:
            if CONVERT_STOP.is_set(): break
            try: items.extend(_expand_link(link, args, log_cb))
            except core.StopDownload: break
            except Exception as e:
                failed += 1; log_cb(f"Could not read {link}: {clean_msg(e)}")
        seen_ids, unique = set(), []
        for u in items:                                   # the same video pasted twice is only converted once
            m = _VIDEO_ID.search(u)
            key = m.group(1) if m else u
            if key not in seen_ids:
                seen_ids.add(key); unique.append(u)
        items = unique
        total = len(items)
        for n, vurl in enumerate(items, 1):
            if CONVERT_STOP.is_set(): break
            status_cb(f"Downloading {n}/{total}...")
            bar_cb(0)
            raw = None
            try:
                opts = core.ydl_options(args, thread_name="QC", format="bestaudio/best", outtmpl=str(temp / f"{n}_%(id)s.%(ext)s"),
                                        noplaylist=True, ffmpeg_location=ffmpeg_exe)
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(vurl, download=True)
                    raw = Path(ydl.prepare_filename(info))
                vtitle = info.get("title") or "video"
                if info.get("track") and info.get("artist"):
                    artist, song = info["artist"], info["track"]
                    name = f"{artist} - {song}"
                else:
                    artist, song = youtube_artist_title(vtitle, info.get("channel") or info.get("uploader") or "")
                    name = vtitle
                stem, k = out / core.safe_name(name), 2
                while Path(f"{stem}.{ext}").exists():
                    stem = out / f"{core.safe_name(name)} ({k})"; k += 1
                status_cb(f"Converting {n}/{total}: {song}")
                bar_cb(1)
                final = core.convert_audio(raw, stem, fmt, quality, part_id="qc", stop_check=CONVERT_STOP.is_set)
                core.tag_mp3(final, core.Track(artist, song, "", 0, vurl))
                ok += 1
            except core.StopDownload:
                break
            except Exception as e:
                failed += 1; log_cb(f"Failed: {vurl}: {clean_msg(e)}")
                if raw: raw.unlink(missing_ok=True)
            if n < total and delay > 0 and CONVERT_STOP.wait(delay):
                break
    finally:
        core.STOP_CHECK_CALLBACK, core.PROGRESS_CALLBACK = prev_stop, prev_prog
        shutil.rmtree(temp, ignore_errors=True)
        for f in (BASE_DIR / ".cache").glob("cookies_PL.txt"): f.unlink(missing_ok=True)
        for f in (BASE_DIR / ".cache").glob("cookies_QC.txt"): f.unlink(missing_ok=True)
    return ok, failed, str(out)

def jobs_digest(all_jobs) -> str:
    payload = json.dumps([[a, t.__dict__] for a, t in all_jobs], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()

def save_state(completed_indices, failed_indices):
    with STATE_LOCK:
        payload = json.dumps({"batch": _BATCH_DIGEST, "completed": sorted(completed_indices), "failed": sorted(failed_indices)})
    try: atomic_write_text(STATE_FILE, payload)
    except Exception: pass

def load_progress(digest: str):
    """Progress only counts if it was saved for exactly this job list."""
    if not STATE_FILE.exists():
        return set(), set()
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("batch") != digest:
            return set(), set()
        return set(state.get("completed", [])), set(state.get("failed", []))
    except Exception:
        return set(), set()

def load_jobs(fingerprint: dict):
    """Return the saved job list, or None when this batch should be rebuilt."""
    if not JOBS_FILE.exists():
        return None
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        if data.get("fingerprint") != fingerprint:
            return None
        raw = data.get("jobs", [])
    elif isinstance(data, list):
        raw = data
    else:
        return None
    try:
        return [(item["artist"], core.Track(**item["track"])) for item in raw]
    except Exception:
        return None

def batch_fingerprint(csv_path: str, download_mode: str, limit: int, max_duration: int, block_words: str, output_format: str, out_dir: str = "", input_type: str = "spotify") -> dict:
    mtime = 0
    if not looks_like_url(csv_path):
        try:
            path = Path(csv_path)
            mtime = path.stat().st_mtime if path.exists() else 0
        except (OSError, ValueError):
            mtime = 0
    return {
        "input": input_type,
        "csv": str(csv_path),
        "mode": download_mode,
        "limit": limit,
        "max_duration": max_duration,
        "block_words": block_words,
        "format": output_format,
        "out_dir": str(Path(out_dir).resolve()) if out_dir else "",
        "mtime": mtime,
    }

def prune_failure_log(log_path: Path, succeeded: set) -> None:
    """Drop failure entries for tracks that have since succeeded."""
    for h in list(core.fail_log.handlers):
        try: h.close()
        except Exception: pass
        core.fail_log.removeHandler(h)
    if not log_path.exists():
        return
    try:
        kept = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            parts = line.split(" | ")
            if len(parts) >= 3 and parts[1] in succeeded:
                continue
            kept.append(line)
        atomic_write_text(log_path, "\n".join(kept) + ("\n" if kept else ""))
    except Exception:
        pass

def run_gui_batch(*args, **kwargs):
    """Run one batch. Guarantees the CPU monitor stops and cookie copies are removed, even on error."""
    monitor_stop = threading.Event()
    try:
        return _run_gui_batch(monitor_stop, *args, **kwargs)
    finally:
        monitor_stop.set()
        for f in (BASE_DIR / ".cache").glob("cookies_*.txt"):
            try: f.unlink()
            except Exception: pass

def _run_gui_batch(monitor_stop, csv_path: str, out_dir: str, limit: int, quality: str, delay: float, update_ui_cb, track_start_cb, track_end_cb, log_cb, cpu_threads: int,
                  retry_only: bool = False, primary_source: str = "youtube", max_duration: int = 600, download_mode: str = "full_artist", cpu_profile: str = "smart",
                  output_format: str = "mp3", convert_start_cb=None, convert_end_cb=None, block_words: str = "", cookie_path: str = "",
                  input_type: str = "spotify"):
    global CPU_PROFILE, LOG_CB, GLOBAL_CPU, _BATCH_DIGEST
    CPU_PROFILE, LOG_CB = cpu_profile, log_cb
    # NOTE: STOP_EVENT is deliberately NOT cleared here. The caller clears it before starting the worker,
    # so a STOP pressed right after START can't be erased by the backend.
    core.STOP_CHECK_CALLBACK, core.THROTTLE_CALLBACK = is_stopped, check_hardware_throttle

    if not core.fail_log.handlers:
        h = logging.FileHandler(BASE_DIR / "failed_items.txt", encoding="utf-8", mode="a")
        h.setFormatter(logging.Formatter("%(message)s"))
        core.fail_log.addHandler(h)

    def cpu_monitor():
        global GLOBAL_CPU
        psutil.cpu_percent(interval=None)
        while not monitor_stop.wait(1.0):
            GLOBAL_CPU = psutil.cpu_percent(interval=None)
    threading.Thread(target=cpu_monitor, daemon=True).start()

    words = [w.strip() for w in block_words.split(",") if w.strip()]
    skip_regex = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else re.compile(r"^$", re.I)

    class Args: pass
    args = Args()
    args.out, args.limit, args.quality, args.tolerance, args.delay = Path(out_dir), limit, quality, 15.0, delay
    args.log, args.cookies_from_browser, args.dry_run, args.cpu_threads = BASE_DIR / "failed_items.txt", cookie_path, False, cpu_threads
    args.primary_source, args.max_duration, args.cpu_profile = primary_source, max_duration, cpu_profile
    args.output_format = output_format

    all_jobs, completed_indices, failed_indices = [], set(), set()
    lookup_failed = False   # an artist/album lookup failed: don't freeze an incomplete job list
    is_youtube = (input_type == "youtube")
    fingerprint = batch_fingerprint(csv_path, "youtube" if is_youtube else download_mode, limit, max_duration, block_words, output_format, out_dir, input_type)
    if not retry_only:
        loaded_jobs = load_jobs(fingerprint)
        if loaded_jobs is not None:
            all_jobs = loaded_jobs
            _BATCH_DIGEST = jobs_digest(all_jobs)
            completed_indices, failed_indices = load_progress(_BATCH_DIGEST)
            if all_jobs:
                update_ui_cb(len(completed_indices) + len(failed_indices), len(all_jobs), "Resuming...")
    
    if is_youtube and not all_jobs and not retry_only:
        update_ui_cb(0, 0, "Reading YouTube list...")
        try:
            all_jobs = build_youtube_jobs(csv_path, args, max_duration, log_cb, update_ui_cb)
        except core.StopDownload:
            STOP_EVENT.set()
        except Exception as e:
            log_cb(f"Could not read the YouTube list: {clean_msg(e)}")
            all_jobs = []
        if STOP_EVENT.is_set():
            update_ui_cb(0, 0, "Stopped. Progress saved.")
            monitor_stop.set()
            return
        if all_jobs:
            _BATCH_DIGEST = jobs_digest(all_jobs)
            job_payload = {"version": 2, "fingerprint": fingerprint, "jobs": [{"artist": a, "track": t.__dict__} for a, t in all_jobs]}
            try: atomic_write_text(JOBS_FILE, json.dumps(job_payload, ensure_ascii=False))
            except Exception: pass
            save_state(completed_indices, failed_indices)

    if not is_youtube and not all_jobs and not retry_only:
        # Move UI update to prevent freeze
        update_ui_cb(0, 0, "Reading CSV...")
        found_artists, found_albums, playlist_tracks = {}, set(), []
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                artist_cell = next((v for k, v in row.items() if k and k.startswith("Artist Name")), "")
                track_name, album_name, duration_ms = row.get("Track Name", ""), row.get("Album Name", ""), row.get("Track Duration (ms)", "0")
                try: duration_s = float(duration_ms) / 1000
                except ValueError: duration_s = 0.0
                artists = [a.strip() for a in (artist_cell or "").split(";") if a.strip()]
                
                if download_mode == "playlist":
                    if track_name and artists: playlist_tracks.append(core.Track(artists[0], track_name, album_name, duration_s, ""))
                else:
                    for name in artists:
                        found_artists.setdefault(name.casefold(), [name, 0])[1] += 1
                        if download_mode == "album" and album_name: found_albums.add((name, album_name))

        if download_mode == "playlist":
            for track in playlist_tracks: all_jobs.append((track.artist, track))
        elif download_mode == "top10":
            names = [name for name, _ in sorted(found_artists.values(), key=lambda v: -v[1])]
            for i, name in enumerate(names, 1):
                if STOP_EVENT.is_set(): break
                update_ui_cb(0, 0, f"Top 10: {name} ({i}/{len(names)})...")
                for track in fetch_deezer_top_10(name, max_duration, log_cb): all_jobs.append((track.artist, track))
        else:
            names = [name for name, _ in sorted(found_artists.values(), key=lambda v: -v[1])]
            if download_mode == "album":
                with YoutubeDL(core.ydl_options(args, extract_flat="in_playlist")) as searcher:
                    for i, (artist_name, album_name) in enumerate(found_albums, 1):
                        if STOP_EVENT.is_set(): break
                        update_ui_cb(0, 0, f"Album: {album_name} ({i}/{len(found_albums)})...")
                        try:
                            artist = resolve_artist(artist_name)
                            for t in fetch_catalog(artist, 0, searcher, log_cb, max_duration, skip_regex):
                                if album_name.casefold() in t.album.casefold():
                                    all_jobs.append((artist.get("name", artist_name), t))
                        except core.StopDownload:
                            STOP_EVENT.set(); break
                        except Exception as e: lookup_failed = lookup_failed or not isinstance(e, core.Missing); log_cb(f"Failed album {album_name}: {e}")
            else:
                with YoutubeDL(core.ydl_options(args, extract_flat="in_playlist")) as searcher:
                    for i, name in enumerate(names, 1):
                        if STOP_EVENT.is_set(): break
                        update_ui_cb(0, 0, f"Artist: {name} ({i}/{len(names)})...")
                        try:
                            artist = resolve_artist(name)
                            for t in fetch_catalog(artist, args.limit, searcher, log_cb, max_duration, skip_regex): all_jobs.append((artist.get('name', name), t))
                        except core.StopDownload:
                            STOP_EVENT.set(); break
                        except Exception as e: lookup_failed = lookup_failed or not isinstance(e, core.Missing); log_cb(f"Failed artist {name}: {e}")

        if STOP_EVENT.is_set():
            update_ui_cb(0, 0, "Stopped. Progress saved.")
            monitor_stop.set()
            return

        _BATCH_DIGEST = jobs_digest(all_jobs)
        if lookup_failed:
            log_cb("Some artist lookups failed (connection problem). Downloading what was found; press START again later to pick up the rest.")
        else:
            job_payload = {"version": 2, "fingerprint": fingerprint, "jobs": [{"artist": a, "track": t.__dict__} for a, t in all_jobs]}
            try: atomic_write_text(JOBS_FILE, json.dumps(job_payload, ensure_ascii=False))
            except Exception: pass
            save_state(completed_indices, failed_indices)

    if retry_only and args.log.exists():
        seen_subjects = set()
        for line in args.log.read_text(encoding="utf-8").splitlines():
            parts = line.split(" | ")
            if len(parts) >= 3 and parts[0] in ("MISSING", "FAILED") and " - " in parts[1] and parts[1] not in seen_subjects:
                seen_subjects.add(parts[1])
                artist, title = parts[1].split(" - ", 1)
                link = parts[-1].strip() if len(parts) >= 4 and parts[-1].strip().startswith("http") else ""
                all_jobs.append((artist, core.Track(artist, title, "", 0, link)))

    total_tracks = len(all_jobs)
    if total_tracks == 0:
        update_ui_cb(0, 0, "No tracks found.")
        monitor_stop.set()
        return

    temp_dir = Path(out_dir) / ".temp_downloads"
    temp_dir.mkdir(parents=True, exist_ok=True)

    download_queue = queue.Queue()
    convert_queue = queue.Queue()
    DOWNLOAD_GATE.reset()

    initial_completed = len(completed_indices) + len(failed_indices)

    for i in range(total_tracks):
        if i not in completed_indices and i not in failed_indices:
            download_queue.put((i, all_jobs[i][0], all_jobs[i][1]))
    
    for _ in range(3): download_queue.put(None)

    lock = threading.Lock()
    start_time = time.time()
    consecutive_fails = 0  # Backoff counter
    succeeded_subjects = set()  # "artist - title" entries to prune from failed_items.txt
    
    def calculate_eta():
        tracks_done = len(completed_indices) + len(failed_indices)
        session_done = tracks_done - initial_completed
        if session_done < 3: return "Calculating..."
        elapsed = time.time() - start_time
        avg = elapsed / session_done
        rem = (total_tracks - tracks_done) * avg
        return f"{int(rem//60)}m {int(rem%60)}s"

    def download_worker():
        nonlocal consecutive_fails
        thread_id = f"DL-{threading.current_thread().name[-1]}"
        pending = None   # a track to retry after a network outage (the queue already holds its end-markers)
        net_attempts = 0
        while True:
            if STOP_EVENT.is_set(): break
            if pending is not None:
                item, pending = pending, None
            else:
                item = download_queue.get()
                net_attempts = 0
            if item is None: break

            idx, artist, track = item
            ext = "m4a" if output_format == "m4a" else ("opus" if output_format == "opus" else "mp3")
            target_path = Path(out_dir) / core.safe_name(artist) / f"{core.safe_name(track.title)}.{ext}"

            if (target_path.exists() and target_path.stat().st_size > 0) or idx in completed_indices:
                convert_queue.put((idx, artist, track, None))
                continue

            try:
                if DOWNLOAD_GATE.wait(delay): break
                check_hardware_throttle()
                with lock: track_start_cb(artist, track.title, len(completed_indices) + len(failed_indices), total_tracks)
                
                if track.url.startswith("http") and (is_youtube or retry_only):
                    url = track.url   # a YouTube link we already know: download it directly, no search
                else:
                    with YoutubeDL(core.ydl_options(args, thread_name=thread_id, extract_flat="in_playlist")) as searcher:
                        url = core.find_audio_source(searcher, track, 15.0, primary_source, max_duration)
                if not url: raise core.Missing("No URL")
                
                raw_path = core.download_raw(url, temp_dir, artist, track.title, args, thread_id, job_id=str(idx))
                convert_queue.put((idx, artist, track, raw_path))

                with lock: consecutive_fails = 0 # Reset backoff on success
            except core.StopDownload:
                break
            except Exception as e:
                if is_network_error(e):
                    if not internet_up():
                        # We're offline: don't burn through the queue. Wait for the connection, then retry this same track.
                        with lock: log_cb("Internet disconnected. Waiting for connection...")
                        while not internet_up():
                            if pause_or_stop(10): break
                        if STOP_EVENT.is_set(): break
                        with lock: log_cb("Connection restored. Retrying the same track...")
                        pending = item
                        continue
                    if net_attempts < 3:
                        # Online, but one host failed to resolve: retry a few times before counting it as failed.
                        net_attempts += 1
                        pending = item
                        if pause_or_stop(5): break
                        continue
                do_backoff = False
                with lock:
                    log_cb(f"Failed to download {track.title}: {clean_msg(e)}")
                    core.record("FAILED", f"{artist} - {track.title}", e, track.url if track.url.startswith("http") and is_youtube else "")
                    failed_indices.add(idx)
                    consecutive_fails += 1
                    if not retry_only: save_state(completed_indices, failed_indices)
                    track_end_cb(len(completed_indices) + len(failed_indices), total_tracks, calculate_eta())
                    if consecutive_fails >= 5:
                        consecutive_fails = 0
                        do_backoff = True
                # Back off OUTSIDE the lock so other workers and the GUI keep running, and stay stoppable.
                if do_backoff:
                    log_cb("5 consecutive failures. Backing off for 60 seconds...")
                    if pause_or_stop(60):
                        break

    def convert_worker():
        thread_id = f"CV-{threading.current_thread().name[-1]}"
        while True:
            if STOP_EVENT.is_set(): break
            item = convert_queue.get()
            if item is None: break
            
            idx, artist, track, raw_path = item
            final_stem = Path(out_dir) / core.safe_name(artist) / core.safe_name(track.title)
            
            if raw_path is None:
                with lock:
                    completed_indices.add(idx)
                    succeeded_subjects.add(f"{artist} - {track.title}")
            else:
                if convert_start_cb: convert_start_cb(thread_id, track.title)
                stopped = False
                try:
                    check_hardware_throttle()
                    target_path = core.convert_audio(raw_path, final_stem, output_format, quality, part_id=str(idx), stop_check=is_stopped)
                    core.tag_mp3(target_path, track)
                    with lock:
                        completed_indices.add(idx)
                        succeeded_subjects.add(f"{artist} - {track.title}")
                except core.StopDownload:
                    # A user stop is not a failure: leave the track pending so it resumes next time.
                    stopped = True
                except Exception as e:
                    with lock:
                        log_cb(f"Failed to convert {track.title}: {clean_msg(e)}")
                        core.record("FAILED", f"{artist} - {track.title}", e, track.url if track.url.startswith("http") and is_youtube else "")
                        failed_indices.add(idx)

                if convert_end_cb: convert_end_cb(thread_id)
                if stopped: break

                with lock:
                    if not retry_only: save_state(completed_indices, failed_indices)
                    track_end_cb(len(completed_indices) + len(failed_indices), total_tracks, calculate_eta())

    d_threads = [threading.Thread(target=download_worker, name=f"T{i}") for i in range(1, 4)]
    c_threads = [threading.Thread(target=convert_worker, name=f"T{i}") for i in range(1, 3)]

    for t in d_threads: t.start()
    for t in c_threads: t.start()
    for t in d_threads: t.join()
    
    for _ in c_threads: convert_queue.put(None)
    for t in c_threads: t.join()

    shutil.rmtree(temp_dir, ignore_errors=True)

    if succeeded_subjects:
        prune_failure_log(args.log, succeeded_subjects)

    tracks_done = len(completed_indices) + len(failed_indices)
    if not STOP_EVENT.is_set() and tracks_done >= total_tracks:
        update_ui_cb(total_tracks, total_tracks, "Batch Complete!")
        if not retry_only:   # a retry run must not erase the resume data of the main batch
            if STATE_FILE.exists(): STATE_FILE.unlink()
            if JOBS_FILE.exists(): JOBS_FILE.unlink()
    else:
        update_ui_cb(tracks_done, total_tracks, "Stopped. Progress saved.")
