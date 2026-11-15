# Music Downloader

A simple desktop app that turns a **Spotify playlist** or a **YouTube playlist** into a folder of audio files (M4A, MP3 or Opus), organised as `Artist/Song`. It also has a **Quick Converter** for turning any YouTube link into audio.

Plain black-and-white interface. Windows and Linux.

> Use it for music you have the right to download. See [Disclaimer](#disclaimer).

---

## Download

Go to the [**Releases**](../../releases) page and grab the file for your system:

| System | File | How to run |
|---|---|---|
| Windows | `MusicDownloader.exe` | Double-click it. |
| Linux (any recent distro) | `MusicDownloader.AppImage` | `chmod +x MusicDownloader.AppImage`, then `./MusicDownloader.AppImage` |

Linux note: if it won't start, install FUSE (`sudo apt install libfuse2` on Ubuntu/Debian), or run it with `./MusicDownloader.AppImage --appimage-extract-and-run`. 64-bit x86 only.

No installation and no Python needed. ffmpeg and everything else is bundled.

---

## First launch

A setup window asks for two things:

1. **YouTube cookies (required).** YouTube blocks automated downloads without them.
   - Install the "Get cookies.txt LOCALLY" extension (links are in the setup window, for Chrome and Firefox).
   - Open youtube.com while logged in, use the extension to export `cookies.txt`, and select that file in the app.
2. **Your playlist (optional, can be set later):**
   - **Spotify CSV**: export your playlist with [Exportify](https://exportify.net) and pick the CSV.
   - **YouTube link**: paste a YouTube playlist link (a single video link works too).
   - **YouTube CSV**: a CSV or text file with one YouTube link per line, or a Google Takeout playlist CSV.

Your cookies and settings stay on your own computer. They are never uploaded anywhere.

---

## Using it

The main window has:

- **Spotify / YouTube switch** next to the playlist box. Each side remembers its own file or link.
- **Save to**: the folder your music goes into.
- **START / STOP / RETRY**: start the batch, stop it safely (progress is saved), or retry tracks that failed.
- **Convert**: opens the Quick Converter.
- **Settings**: advanced options and a live log with per-worker progress bars.

### Spotify modes (Settings → Mode)

| Mode | What it downloads |
|---|---|
| Full Artist Catalog | Every album/EP by every artist in your playlist |
| Top 10 Songs | Each artist's ten most popular tracks |
| Playlist Tracks Only | Only the songs in your CSV (fastest) |
| Original Albums | The albums your songs came from |

YouTube playlists always download exactly the videos in the list. Mode is not used.

### Quick Converter

Paste one or more YouTube links (one per line). A playlist link converts every video in it. Choose **MP3, M4A or Opus**, the quality, and a folder, then press **CONVERT**.

---

## Staying safe from YouTube bans

- Keep **Delay** at 2–3 seconds or more. Under 0.7 seconds the app warns you, because that is how IPs get temporarily blocked.
- Use your cookie file and reload it (Settings → Reload Cookies) if you start seeing bot errors.
- If your internet drops, the app waits for it to come back and retries the same track. It doesn't mark the songs as failed.

## Good to know

- **Resume:** stop any time. Press START again with the same settings and it continues where it stopped. Songs already downloaded are skipped.
- **Max Length** skips anything longer than your limit, which helps avoid DJ sets and podcasts.
- **Block Words** skips titles containing words like live, remix, karaoke (Spotify modes).
- Where files are kept: on Windows, next to the exe. On Linux, settings are in `~/.local/share/MusicDownloader` and music defaults to `~/Music/MusicDownloader`.

---

## Run from source

```bash
git clone https://github.com/GBRDL/music-downloader.git
cd music-downloader
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

You also need [ffmpeg](https://ffmpeg.org/download.html) on your PATH, and [Deno](https://deno.com) is recommended for yt-dlp's YouTube support. On Linux you need Tk (`sudo apt install python3-tk`).

## Building the apps

- **Windows exe:** `build_exe.bat` (needs a `.venv`, `ffmpeg.exe` and `deno.exe` next to the scripts).
- **Linux AppImage:** built automatically by GitHub Actions (**Actions → Build Linux AppImage → Run workflow**, or push a tag like `v1.0` to publish it under Releases). To build locally on Linux or WSL: `./build_linux.sh`.

## Built with

[yt-dlp](https://github.com/yt-dlp/yt-dlp), [ffmpeg](https://ffmpeg.org), [customtkinter](https://github.com/TomSchimansky/CustomTkinter), [MusicBrainz](https://musicbrainz.org) (artist catalogs), [Deezer](https://developers.deezer.com) (top tracks) and [Exportify](https://exportify.net) (Spotify export).

## License

[MIT](LICENSE)

## Disclaimer

This project is for personal use. Downloading content may violate the terms of service of YouTube and other sites, and copyrighted music may only be downloaded where you have the right to do so. You are responsible for how you use it. The project is not affiliated with YouTube, Spotify, Google or any of the services above.
