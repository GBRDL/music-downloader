# 🎵 Music Downloader

Paste in a Spotify or YouTube playlist, press START, and get a folder of music. That's the whole idea.

It makes M4A, MP3 or Opus files, sorted into `Artist/Song`, and it has a little **Quick Converter** for turning any YouTube link into audio. The window is deliberately plain: black, white, and boxes. Works on Windows and Linux.

---

## ⚠️ Please read this first

**This is version 1, and it's a hobby project. Expect bugs.**

- **About 50% of this app is vibe coded** with an AI assistant, and **about 50% I did by hand.** I'm not a professional developer. I wrote what I could, asked for help with the rest, and tested it on my own computer.
- **It has only been tested on my own setup so far.** It might misbehave on yours. It might crash, skip songs, name a file strangely, or do something I never thought of.
- **Some things I haven't been able to test properly yet**, like the Linux AppImage on different distros.
- **Don't trust it with anything important.** It only downloads music files, but please don't point it at a folder you can't afford to clutter.

### 🐛 Found a bug? Please tell me!

That's the most helpful thing you can do. I really do want to fix these.

1. Go to the [**Issues**](../../issues) tab and press **New issue**.
2. Tell me what you did, what you expected, and what happened instead.
3. If the app showed an error, paste it in. The log under **Settings** in the app is the best thing to copy from.
4. Mention your system (Windows 11? Ubuntu 24.04?) and whether you used a Spotify CSV, a YouTube link, or the Quick Converter.

Even a short "it crashed when I did X" is useful. Comments, ideas and "this confused me" notes are welcome too. Nothing is too small.

> Please don't paste your cookies file or anything private into an issue. The error text is enough.

---

## What you can do with it

- **Download a Spotify playlist.** Export it with [Exportify](https://exportify.net) and give the app the CSV.
- **Download a YouTube playlist.** Paste the link, or give it a CSV or text file full of links.
- **Convert any YouTube link** to MP3, M4A or Opus with the Quick Converter.
- **Stop whenever you like.** It remembers where it was and picks up again when you press START.
- **Survive a bad connection.** If your Wi-Fi drops, it waits and retries instead of marking songs as failed.

---

## Download

Go to the [**Releases**](../../releases) page and take the file for your system:

| System | File | How to run it |
|---|---|---|
| Windows | `MusicDownloader.exe` | Double-click it. |
| Linux | `MusicDownloader.AppImage` | `chmod +x MusicDownloader.AppImage`, then `./MusicDownloader.AppImage` |

You don't need to install Python or anything else. ffmpeg and the other tools are packed inside.

A few things worth knowing:
- **Windows** may show a "Windows protected your PC" warning, because the app isn't signed. Click **More info → Run anyway** if you trust it.
- **Linux:** if it won't open, install FUSE (`sudo apt install libfuse2` on Ubuntu or Debian), or start it with `./MusicDownloader.AppImage --appimage-extract-and-run`. It's for 64-bit Intel/AMD computers only.

---

## First time setup

When you open it the first time, a setup window asks for two things.

**1. YouTube cookies (required).** YouTube blocks downloads from programs that aren't logged in. To get around that, you give the app a cookies file from your browser:
- Install the "Get cookies.txt LOCALLY" extension (the setup window has buttons for Chrome and Firefox).
- Open youtube.com while logged in, use the extension to export `cookies.txt`, and choose that file in the app.

Your cookies stay on your own computer. The app never uploads them anywhere.

**2. Your playlist (optional, you can do it later):**
- **Spotify CSV**: a file exported from Exportify.
- **YouTube link**: a playlist link (a single video link works too).
- **YouTube CSV**: a CSV or text file with one YouTube link per line, or a Google Takeout playlist CSV.

---

## How to use it

The main window is simple:

- **Spotify / YouTube switch**, right next to the playlist box. Each side remembers its own file or link.
- **Save to**, which is the folder your music goes into.
- **START / STOP / RETRY**: start, stop safely (progress is saved), or retry songs that failed.
- **Convert** opens the Quick Converter.
- **Settings** has the extra options and a live log.

### Spotify modes

Under **Settings → Mode**, for Spotify playlists only:

| Mode | What you get |
|---|---|
| Full Artist Catalog | Every album and EP from every artist in your playlist (this takes a long time) |
| Top 10 Songs | Each artist's ten most popular tracks |
| Playlist Tracks Only | Just the songs in your CSV (the fastest) |
| Original Albums | The albums your songs came from |

YouTube playlists always download exactly the videos in the list.

### Quick Converter

Paste one or more YouTube links, one per line. A playlist link converts every video inside it. Pick **MP3, M4A or Opus**, the quality and a folder, and press **CONVERT**.

---

## Please don't get yourself banned

YouTube doesn't love automated downloads, so the app tries to be gentle:

- Keep the **Delay** at 2–3 seconds or more. The app warns you if you go under 0.7 seconds, because that's how IPs get temporarily blocked.
- If you start seeing bot errors, reload your cookies in **Settings → Reload Cookies**.

---

## Other things worth knowing

- **Max Length** skips anything longer than the limit you set, which helps avoid DJ sets and podcasts.
- **Block Words** skips titles with words like live, remix or karaoke (Spotify modes only).
- **Full Artist Catalog** relies on MusicBrainz, a free service that can be slow or drop connections. If a few artists fail, just press START again. The ones that worked are remembered.
- **Where things are kept:** settings, progress, the failed-songs list and caches live in one folder of their own: `%APPDATA%\MusicDownloader` on Windows (paste that into the File Explorer address bar) and `~/.local/share/MusicDownloader` on Linux. Your music goes wherever you set **Save to**, which defaults to `Music/MusicDownloader` in your home folder. Delete the settings folder to reset the app. Your cookies file stays wherever you saved it.

---

## For developers

**Run from source:**

```bash
git clone https://github.com/GBRDL/music-downloader.git
cd music-downloader
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

You also need [ffmpeg](https://ffmpeg.org/download.html) on your PATH, and [Deno](https://deno.com) is recommended for yt-dlp's YouTube support. On Linux you need Tk too (`sudo apt install python3-tk`).

**Building the apps:**
- **Windows exe:** `build_exe.bat` (needs a `.venv`, plus `ffmpeg.exe` and `deno.exe` next to the scripts).
- **Linux AppImage:** built by GitHub Actions. Go to **Actions → Build Linux AppImage → Run workflow**, or publish a release with a tag like `v1.0` and it attaches the file for you. You can also run `./build_linux.sh` on Linux or WSL.

Pull requests are welcome, but remember that this is a first version, so the code is a bit rough.

---

## Thanks to

[yt-dlp](https://github.com/yt-dlp/yt-dlp), [ffmpeg](https://ffmpeg.org), [customtkinter](https://github.com/TomSchimansky/CustomTkinter), [MusicBrainz](https://musicbrainz.org), [Deezer](https://developers.deezer.com) and [Exportify](https://exportify.net). This project would be nothing without them.

## License

[MIT](LICENSE). Do what you like with it.

## Legal stuff

This project is for personal use. Downloading content can go against the terms of YouTube and other sites, and copyrighted music should only be downloaded where you have the right to do so. You are responsible for how you use this app. It isn't affiliated with YouTube, Spotify, Google or any of the services above.
