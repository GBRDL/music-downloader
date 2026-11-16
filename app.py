import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading, time, psutil, json, webbrowser, sys, os, queue, subprocess
from collections import deque
from pathlib import Path
import playlist_to_artists_mp3 as backend
import tkinter as tk

if getattr(sys, 'frozen', False):
    BASE_DIR = backend.BASE_DIR
    ICON_PATH = Path(sys._MEIPASS) / "app.ico"
else:
    BASE_DIR = Path(__file__).parent
    ICON_PATH = BASE_DIR / "app.ico"
SETTINGS_FILE = BASE_DIR / "settings.json"
# Windows/dev: a "downloads" folder next to the app. Packaged Linux app (read-only AppImage): ~/Music/MusicDownloader.
DEFAULT_OUT = str(Path.home() / "Music" / "MusicDownloader") if (getattr(sys, "frozen", False) and sys.platform.startswith("linux")) else "downloads"

THEME = "dark"   # "dark" = plain black, "light" = plain white
if THEME == "light":
    BG_MAIN = "#FFFFFF"; BG_CARD = "#FFFFFF"; BORDER = "#8A8A8A"; TEXT_MAIN = "#000000"; TEXT_DIM = "#555555"
    ACCENT = "#000000"; ACCENT_HOVER = "#333333"; ON_ACCENT = "#FFFFFF"; PROGRESS_BG = "#DDDDDD"; PROGRESS_FG = "#000000"; SEG_SELECTED = "#CCCCCC"
else:
    BG_MAIN = "#000000"; BG_CARD = "#000000"; BORDER = "#555555"; TEXT_MAIN = "#FFFFFF"; TEXT_DIM = "#AAAAAA"
    ACCENT = "#FFFFFF"; ACCENT_HOVER = "#CCCCCC"; ON_ACCENT = "#000000"; PROGRESS_BG = "#333333"; PROGRESS_FG = "#FFFFFF"; SEG_SELECTED = "#555555"
STOP_RED = "#C00000"; STOP_HOVER = "#990000"

class ToolTip:
    def __init__(self, widget, text, delay=400): 
        self.widget = widget; self.text = text; self.delay = delay; self.tipwindow = None; self.id = None
        self.widget.bind("<Enter>", self.enter); self.widget.bind("<Leave>", self.leave)
    def enter(self, event=None): self.id = self.widget.after(self.delay, self.showtip)
    def leave(self, event=None):
        if self.id: self.widget.after_cancel(self.id); self.id = None
        self.hidetip()
    def showtip(self, event=None):
        x = self.widget.winfo_rootx() + 20; y = self.widget.winfo_rooty() + 20
        self.tipwindow = tw = tk.Toplevel(self.widget); tw.wm_overrideredirect(True); tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify=tk.LEFT, background=BG_CARD, foreground=TEXT_MAIN, relief=tk.SOLID, borderwidth=1, font=("Segoe UI", "9")).pack(ipadx=6, ipady=4)
    def hidetip(self):
        if self.tipwindow: self.tipwindow.destroy(); self.tipwindow = None

class RiskPopup(ctk.CTkToplevel):
    def __init__(self, parent, delay_var, revert_cb):
        super().__init__(parent)
        self.delay_var = delay_var; self.revert_cb = revert_cb
        self.title("HIGH RISK WARNING"); self.geometry("500x350"); self.resizable(False, False); self.configure(fg_color=BG_MAIN)
        self.grab_set(); self.transient(parent); self.protocol("WM_DELETE_WINDOW", self.on_close)
        ctk.CTkLabel(self, text="DANGER", font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"), text_color=STOP_RED).pack(pady=(30, 10))
        ctk.CTkLabel(self, text="You have set the delay extremely low.", font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_MAIN).pack()
        ctk.CTkLabel(self, text="Setting the delay below 0.7 seconds forces YouTube to\nreceive requests too quickly, treating your IP like a bot.\n\nThis will result in either a TEMPORARY or PERMANENT IP BAN.", font=ctk.CTkFont(size=13), text_color=TEXT_DIM, justify="center").pack(pady=20)
        btn_frame = ctk.CTkFrame(self, fg_color="transparent"); btn_frame.pack(fill="x", side="bottom", padx=20, pady=20)
        ctk.CTkButton(btn_frame, text="Revert to Safe (3.0s)", width=200, height=40, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, command=self.revert_safe).pack(side="left")
        ctk.CTkButton(btn_frame, text="Accept the Risk", width=200, height=40, corner_radius=0, fg_color=STOP_RED, hover_color=STOP_HOVER, command=self.accept_risk).pack(side="right")
    def on_close(self): self.revert_safe()
    def revert_safe(self): self.delay_var.set("3.0"); self.destroy()
    def accept_risk(self): self.destroy()

class SetupWizard(ctk.CTkToplevel):
    KINDS = ["Spotify CSV", "YouTube link", "YouTube CSV"]
    HINTS = {
        "Spotify CSV": "Use Exportify to download your Spotify playlist as a CSV, then select it.",
        "YouTube link": "Paste the link of a YouTube playlist (a single video link works too).",
        "YouTube CSV": "A CSV or text file of YouTube links (one per line), or a Google Takeout playlist CSV.",
    }
    def __init__(self, parent, cookie_var, save_cb):
        super().__init__(parent)
        self.app = parent; self.cookie_var = cookie_var; self.save_cb = save_cb
        self.title("Initial Setup"); self.geometry("650x740"); self.resizable(False, False); self.configure(fg_color=BG_MAIN)
        self.grab_set(); self.transient(parent); self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.after(300, self._apply_icon)
        self.native_font = ctk.CTkFont(family="Segoe UI", size=13)
        yt = parent.youtube_input
        yt_is_link = backend.looks_like_url(yt)
        self.values = {"Spotify CSV": parent.spotify_csv, "YouTube link": yt if yt_is_link else "", "YouTube CSV": "" if yt_is_link else yt}
        if parent.input_type_var.get() == "YouTube": start = "YouTube CSV" if (yt and not yt_is_link) else "YouTube link"
        else: start = "Spotify CSV"
        self.kind_var = ctk.StringVar(value=start); self._shown = start
        self.pl_var = ctk.StringVar(value=self.values[start])
        self.build_ui()
    def _apply_icon(self):
        try:
            if ICON_PATH.exists(): self.iconbitmap(str(ICON_PATH))
        except Exception: pass
    def build_ui(self):
        ctk.CTkLabel(self, text="Welcome to Music Downloader", font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"), text_color=TEXT_MAIN).pack(pady=(25, 5))
        ctk.CTkLabel(self, text="Let's get you set up. You cannot use the app without valid cookies.", font=self.native_font, text_color=TEXT_DIM).pack(pady=(0, 20))
        frame1 = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_CARD, border_width=1, border_color=BORDER); frame1.pack(fill="x", padx=25, pady=10)
        ctk.CTkLabel(frame1, text="1. YouTube Cookies (Required)", font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"), text_color=TEXT_MAIN).pack(anchor="w", padx=20, pady=(15, 5))
        ctk.CTkLabel(frame1, text="YouTube blocks automated downloads without cookies.\nYou must export them using the browser extension.", text_color=TEXT_DIM, justify="left").pack(anchor="w", padx=20)
        btn_frame1 = ctk.CTkFrame(frame1, fg_color="transparent"); btn_frame1.pack(fill="x", padx=20, pady=10)
        ctk.CTkButton(btn_frame1, text="Get Chrome Extension", corner_radius=0, fg_color=BG_MAIN, text_color=TEXT_MAIN, hover_color=BORDER, command=lambda: webbrowser.open("https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_frame1, text="Get Firefox Extension", corner_radius=0, fg_color=BG_MAIN, text_color=TEXT_MAIN, hover_color=BORDER, command=lambda: webbrowser.open("https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/")).pack(side="left")
        self.cookie_status = ctk.CTkLabel(frame1, text="Cookies Not Loaded", text_color=STOP_RED, font=self.native_font); self.cookie_status.pack(anchor="w", padx=20, pady=(5, 5))
        ctk.CTkButton(frame1, text="Select Cookie File", corner_radius=0, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self.load_cookies).pack(pady=(0, 20))

        frame2 = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_CARD, border_width=1, border_color=BORDER); frame2.pack(fill="x", padx=25, pady=10)
        ctk.CTkLabel(frame2, text="2. Your Playlist (Optional)", font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"), text_color=TEXT_MAIN).pack(anchor="w", padx=20, pady=(15, 5))
        ctk.CTkLabel(frame2, text="Choose where your songs come from. You can change this later in the main window.", text_color=TEXT_DIM, justify="left").pack(anchor="w", padx=20, pady=(0, 8))
        self.pl_seg = ctk.CTkSegmentedButton(frame2, values=self.KINDS, variable=self.kind_var, command=self.refresh_playlist_ui, corner_radius=0, border_width=2, selected_color=SEG_SELECTED, selected_hover_color=SEG_SELECTED, unselected_color=BG_MAIN, unselected_hover_color=BORDER, fg_color=BORDER, text_color=TEXT_MAIN, font=self.native_font)
        self.pl_seg.pack(anchor="w", padx=20, pady=(0, 8))
        self.pl_hint = ctk.CTkLabel(frame2, text=self.HINTS[self.kind_var.get()], text_color=TEXT_DIM, justify="left", wraplength=560); self.pl_hint.pack(anchor="w", padx=20)
        self.exportify_btn = ctk.CTkButton(frame2, text="Open Exportify", corner_radius=0, fg_color=BG_MAIN, text_color=TEXT_MAIN, hover_color=BORDER, command=lambda: webbrowser.open("https://exportify.net"))
        self.pl_row = ctk.CTkFrame(frame2, fg_color="transparent"); self.pl_row.pack(fill="x", padx=20, pady=10)
        self.pl_entry = ctk.CTkEntry(self.pl_row, textvariable=self.pl_var, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_MAIN, text_color=TEXT_MAIN)
        self.pl_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.pl_btn = ctk.CTkButton(self.pl_row, text="Browse", width=90, corner_radius=0, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self.pick_playlist)
        self.pl_btn.pack(side="left", padx=(10, 0))
        self.csv_status = ctk.CTkLabel(frame2, text="", text_color=TEXT_DIM, font=self.native_font); self.csv_status.pack(anchor="w", padx=20, pady=(0, 15))
        self.pl_var.trace_add("write", lambda *a: self.update_playlist_status())
        self.finish_btn = ctk.CTkButton(self, text="Finish Setup", font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"), height=45, corner_radius=0, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, state="disabled", command=self.finish_setup)
        self.finish_btn.pack(fill="x", padx=25, pady=15)
        self.refresh_playlist_ui(); self.check_status()
    def refresh_playlist_ui(self, *_):
        old, new = self._shown, self.kind_var.get()
        if old != new:
            self.values[old] = self.pl_var.get(); self._shown = new; self.pl_var.set(self.values[new])
        self.pl_hint.configure(text=self.HINTS[new])
        self.pl_btn.configure(text="Paste" if new == "YouTube link" else "Browse")
        if new == "Spotify CSV": self.exportify_btn.pack(anchor="w", padx=20, pady=(8, 0), after=self.pl_hint)
        else: self.exportify_btn.pack_forget()
        self.update_playlist_status()
    def pick_playlist(self):
        kind = self.kind_var.get()
        if kind == "YouTube link":
            try: text = self.clipboard_get().strip()
            except Exception: text = ""
            if text: self.pl_var.set(text)
            return
        types = [("CSV files", "*.csv")] if kind == "Spotify CSV" else [("CSV or text files", "*.csv *.txt"), ("All files", "*.*")]
        filename = filedialog.askopenfilename(filetypes=types)
        if filename: self.pl_var.set(filename)
    def update_playlist_status(self):
        kind = self.kind_var.get(); text = self.pl_var.get().strip().strip('"')
        if not text: self.csv_status.configure(text="Nothing selected (you can skip this)", text_color=TEXT_DIM)
        elif kind == "YouTube link":
            ok = backend.looks_like_url(text)
            self.csv_status.configure(text="Link looks good" if ok else "That doesn't look like a link", text_color=ACCENT if ok else STOP_RED)
        else:
            ok = Path(text).is_file()
            self.csv_status.configure(text="File found" if ok else "File not found", text_color=ACCENT if ok else STOP_RED)
    def check_status(self):
        path = self.cookie_var.get()
        if path and Path(path).exists() and Path(path).stat().st_size > 50:
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore").lower()
                if "youtube" in content or "netscape" in content:
                    self.cookie_status.configure(text="Cookies Loaded!", text_color=ACCENT); self.finish_btn.configure(state="normal")
                else: self.cookie_status.configure(text="Invalid Cookie File", text_color=STOP_RED); self.finish_btn.configure(state="disabled")
            except Exception: pass
        self.update_playlist_status()
    def load_cookies(self):
        filename = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if filename: self.cookie_var.set(filename); self.check_status()
    def finish_setup(self):
        self.app.apply_wizard_input(self.kind_var.get(), self.pl_var.get().strip().strip('"'))
        self.save_cb(); self.destroy()

class QuickConverter(ctk.CTkToplevel):
    FORMATS = {"MP3": "mp3", "M4A (AAC)": "m4a", "Opus": "opus"}
    def __init__(self, app):
        super().__init__(app)
        self.app = app; self.q = queue.Queue(); self._alive = True; self._last_bar = 0.0
        self.title("Quick Converter"); self.geometry("640x620"); self.minsize(640, 620); self.configure(fg_color=BG_MAIN)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self._apply_icon)
        font = app.native_font; pad = {"padx": 25}
        ctk.CTkLabel(self, text="Quick Converter", font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"), text_color=TEXT_MAIN).pack(anchor="w", pady=(20, 2), **pad)
        ctk.CTkLabel(self, text="Paste one or more YouTube links, one per line.\nA playlist link converts every video in it.", font=font, text_color=TEXT_DIM, justify="left").pack(anchor="w", **pad)
        self.box = ctk.CTkTextbox(self, height=130, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_CARD, text_color=TEXT_MAIN, font=font)
        self.box.pack(fill="x", pady=(10, 10), **pad)
        opts = ctk.CTkFrame(self, fg_color="transparent"); opts.pack(fill="x", pady=(0, 10), **pad)
        ctk.CTkLabel(opts, text="Format:", font=font, text_color=TEXT_MAIN).pack(side="left")
        ctk.CTkOptionMenu(opts, values=list(self.FORMATS), variable=app.qc_format_var, font=font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN).pack(side="left", padx=(8, 20))
        ctk.CTkLabel(opts, text="Quality:", font=font, text_color=TEXT_MAIN).pack(side="left")
        ctk.CTkOptionMenu(opts, values=["128", "192", "256", "320"], variable=app.qc_quality_var, font=font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN).pack(side="left", padx=8)
        folder = ctk.CTkFrame(self, fg_color="transparent"); folder.pack(fill="x", pady=(0, 10), **pad)
        ctk.CTkLabel(folder, text="Save to:", font=font, text_color=TEXT_MAIN).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(folder, textvariable=app.qc_folder_var, font=font, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_CARD, text_color=TEXT_MAIN).pack(side="left", fill="x", expand=True, ipady=3)
        ctk.CTkButton(folder, text="Browse", width=80, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.pick_folder).pack(side="left", padx=(10, 0))
        self.status = ctk.CTkLabel(self, text="Ready", font=app.bold_font, text_color=TEXT_MAIN); self.status.pack(anchor="w", **pad)
        self.bar = ctk.CTkProgressBar(self, height=14, corner_radius=0, progress_color=PROGRESS_FG, fg_color=PROGRESS_BG)
        self.bar.pack(fill="x", pady=(5, 10), **pad); self.bar.set(0)
        self.log = ctk.CTkTextbox(self, height=110, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_MAIN, text_color=TEXT_DIM, font=ctk.CTkFont(family="Consolas", size=11))
        self.log.pack(fill="both", expand=True, pady=(0, 10), **pad); self.log.configure(state="disabled")
        btns = ctk.CTkFrame(self, fg_color="transparent"); btns.pack(fill="x", pady=(0, 20), **pad); btns.grid_columnconfigure((0, 1, 2), weight=1)
        self.go_btn = ctk.CTkButton(btns, text="CONVERT", font=app.bold_font, height=40, corner_radius=0, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self.start)
        self.go_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.stop_btn = ctk.CTkButton(btns, text="STOP", font=app.bold_font, height=40, corner_radius=0, fg_color=STOP_RED, hover_color=STOP_HOVER, text_color="white", state="disabled", command=self.stop)
        self.stop_btn.grid(row=0, column=1, padx=5, sticky="ew")
        ctk.CTkButton(btns, text="OPEN FOLDER", font=app.bold_font, height=40, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.open_folder).grid(row=0, column=2, padx=(5, 0), sticky="ew")
        self.after(100, self._poll)
    def _apply_icon(self):
        try:
            if ICON_PATH.exists(): self.iconbitmap(str(ICON_PATH))
        except Exception: pass
    def pick_folder(self):
        d = filedialog.askdirectory()
        if d: self.app.qc_folder_var.set(d)
    def open_folder(self):
        p = Path(self.app.qc_folder_var.get() or "downloads")
        try:
            p.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32": os.startfile(str(p))
            elif sys.platform == "darwin": subprocess.Popen(["open", str(p)])
            else: subprocess.Popen(["xdg-open", str(p)])
        except Exception: pass
    def _log(self, text):
        self.log.configure(state="normal"); self.log.insert("end", f"{text}\n"); self.log.see("end"); self.log.configure(state="disabled")
    def start(self):
        app = self.app
        if app.is_running:
            messagebox.showinfo("Busy", "A playlist download is running. Wait for it or stop it first.", parent=self); return
        if app.qc_running:
            messagebox.showinfo("Busy", "The previous conversion is still stopping. Try again in a moment.", parent=self); return
        links = [t for t in self.box.get("1.0", "end").split() if backend.looks_like_url(t)]
        if not links:
            messagebox.showerror("Error", "Paste at least one YouTube link.", parent=self); return
        try: delay = max(0.0, float(app.delay_var.get()))
        except ValueError: delay = 3.0
        fmt = self.FORMATS.get(app.qc_format_var.get(), "mp3")
        folder = app.qc_folder_var.get().strip() or "downloads"
        quality, cookie = app.qc_quality_var.get(), app.cookie_path_var.get()
        app.save_settings()
        app.qc_running = True
        backend.CONVERT_STOP.clear()   # reset BEFORE the thread starts, so an early STOP can't be erased
        self.go_btn.configure(state="disabled", text="WORKING..."); self.stop_btn.configure(state="normal", text="STOP")
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        self.status.configure(text="Starting..."); self.bar.set(0)
        t = threading.Thread(target=self._run, args=(links, folder, fmt, quality, cookie, delay), daemon=True)
        app.qc_thread = t; t.start()
    def stop(self):
        backend.CONVERT_STOP.set(); self.stop_btn.configure(state="disabled", text="STOPPING...")
    def _bar(self, pct):
        now = time.time()
        if now - self._last_bar >= 0.1 or pct in (0, 1):
            self._last_bar = now; self.q.put(("bar", pct))
    def _run(self, links, folder, fmt, quality, cookie, delay):
        try:
            ok, failed, out = backend.quick_convert(links, folder, fmt, quality, cookie, delay,
                log_cb=lambda m: self.q.put(("log", m)), status_cb=lambda s: self.q.put(("status", s)), bar_cb=self._bar)
            self.q.put(("done", ok, failed))
        except Exception as e:
            self.q.put(("log", f"ERROR: {backend.clean_msg(e)}")); self.q.put(("done", 0, 1))
        finally:
            self.app.qc_running = False
    def _poll(self):
        if not self._alive: return
        try:
            while True:
                cmd = self.q.get_nowait()
                if cmd[0] == "status": self.status.configure(text=cmd[1])
                elif cmd[0] == "bar": self.bar.set(cmd[1])
                elif cmd[0] == "log": self._log(cmd[1])
                elif cmd[0] == "done":
                    ok, failed = cmd[1], cmd[2]
                    stopped = backend.CONVERT_STOP.is_set()
                    self.status.configure(text=("Stopped. " if stopped else "Done. ") + f"{ok} converted" + (f", {failed} failed" if failed else ""))
                    self.bar.set(1 if (ok and not stopped) else 0)
                    self.go_btn.configure(state="normal", text="CONVERT"); self.stop_btn.configure(state="disabled", text="STOP")
        except queue.Empty: pass
        except Exception: pass
        self.after(100, self._poll)
    def on_close(self):
        self._alive = False
        backend.CONVERT_STOP.set()   # a running conversion unwinds on its own; the app waits for it when closing
        self.app.qc_win = None
        self.destroy()

class MusicApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("light" if THEME == "light" else "dark")
        super().__init__(fg_color=BG_MAIN)
        self.title("Music Downloader"); self.geometry("640x420"); self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self._apply_icon)  # customtkinter sets its own icon at 200ms; override it after that

        self.native_font = ctk.CTkFont(family="Segoe UI", size=13); self.bold_font = ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        self.settings = self.load_settings(); self.load_vars()
        self.is_running = False; self.last_update_time = 0; self.is_advanced = False; self.risk_popup_open = False
        
        self.gui_queue = queue.Queue()
        self.log_buffer = deque(maxlen=800)   # kept even while the Settings panel is closed, so no message is lost
        self.worker_thread = None; self._closing = False
        self.qc_running = False; self.qc_thread = None; self.qc_win = None
        self.build_minimal_ui()
        self.delay_var.trace_add("write", self.update_risk_label)
        self.after(100, self.poll_gui_queue)
        
        if not self.settings.get("setup_complete", False): self.after(500, self.show_wizard)

    def _apply_icon(self):
        try:
            if ICON_PATH.exists(): self.iconbitmap(str(ICON_PATH))
        except Exception: pass

    def show_wizard(self): SetupWizard(self, self.cookie_path_var, self.save_settings)

    # ---- playlist source (Spotify CSV / YouTube link or file): one visible box, one remembered value per source
    def _slot_get(self, kind): return self.youtube_input if kind == "YouTube" else self.spotify_csv

    def _slot_set(self, kind, value):
        if kind == "YouTube": self.youtube_input = value
        else: self.spotify_csv = value

    def _flush_input(self): self._slot_set(self._shown_type, self.csv_path.get())

    def on_input_type_change(self, new):
        if new == self._shown_type: return
        self._flush_input(); self._shown_type = new
        self.csv_path.set(self._slot_get(new)); self._refresh_input_ui()

    def _show_input(self, kind):
        self.input_type_var.set(kind); self._shown_type = kind
        self.csv_path.set(self._slot_get(kind)); self._refresh_input_ui()

    def apply_wizard_input(self, kind, text):
        if not text: return
        if kind == "Spotify CSV": self.spotify_csv = text; self._show_input("Spotify")
        else: self.youtube_input = text; self._show_input("YouTube")

    def _refresh_input_ui(self):
        yt = self.input_type_var.get() == "YouTube"
        if hasattr(self, "input_hint"):
            self.input_hint.configure(text="YouTube playlist link, or a CSV / text file of YouTube links" if yt else "Spotify playlist CSV from Exportify (exportify.net)")
        menu = getattr(self, "mode_menu", None)
        if menu is not None:
            try: menu.configure(state="disabled" if yt else "normal")
            except Exception: pass

    def _validate_input(self):
        value = self.csv_path.get().strip().strip('"')
        if self.input_type_var.get() == "YouTube":
            if not value: return "Paste a YouTube playlist link, or choose a CSV / text file."
            if not backend.looks_like_url(value) and not Path(value).is_file(): return "That is not a link or an existing file."
        elif not value: return "Select an Exportify CSV file."
        return None

    def open_converter(self):
        if self.qc_win is not None:
            try: self.qc_win.lift(); self.qc_win.focus(); return
            except Exception: self.qc_win = None
        self.qc_win = QuickConverter(self)

    def load_vars(self):
        self.spotify_csv = self.settings.get("csv_path", "")
        self.youtube_input = self.settings.get("youtube_input", "")
        t = self.settings.get("input_type", "Spotify"); t = t if t in ("Spotify", "YouTube") else "Spotify"
        self.input_type_var = ctk.StringVar(value=t); self._shown_type = t
        self.csv_path = ctk.StringVar(value=self._slot_get(t))
        self.out_path = ctk.StringVar(value=self.settings.get("out_path", DEFAULT_OUT))
        self.download_mode = ctk.StringVar(value=self.settings.get("download_mode", "Full Artist Catalog"))
        self.limit_var = ctk.StringVar(value=self.settings.get("limit", "200"))
        self.format_var = ctk.StringVar(value=self.settings.get("format", "M4A (AAC)"))
        self.quality_var = ctk.StringVar(value=self.settings.get("quality", "192"))
        self.delay_var = ctk.StringVar(value=self.settings.get("delay", "3.0"))
        self.source_var = ctk.StringVar(value=self.settings.get("source", "YouTube"))
        self.cpu_profile_var = ctk.StringVar(value=self.settings.get("cpu_profile", "Smart Auto"))
        self.max_mins_var = ctk.StringVar(value=self.settings.get("max_mins", "5"))
        self.max_secs_var = ctk.StringVar(value=self.settings.get("max_secs", "0"))
        self.block_words_var = ctk.StringVar(value=self.settings.get("block_words", "live, demo, slowed, reverb, sped up, remix, karaoke, instrumental, acoustic"))
        self.cookie_path_var = ctk.StringVar(value=self.settings.get("cookie_path", ""))
        self.qc_format_var = ctk.StringVar(value=self.settings.get("qc_format", "MP3"))
        self.qc_quality_var = ctk.StringVar(value=self.settings.get("qc_quality", "192"))
        self.qc_folder_var = ctk.StringVar(value=self.settings.get("qc_folder", str(Path(self.settings.get("out_path", DEFAULT_OUT)) / "Converted")))

    def load_settings(self):
        if SETTINGS_FILE.exists():
            try: return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception: pass
        return {}

    def save_settings(self):
        self._flush_input()
        data = {"input_type": self.input_type_var.get(), "csv_path": self.spotify_csv, "youtube_input": self.youtube_input, "qc_format": self.qc_format_var.get(), "qc_quality": self.qc_quality_var.get(), "qc_folder": self.qc_folder_var.get(), "out_path": self.out_path.get(), "download_mode": self.download_mode.get(), "limit": self.limit_var.get(), "format": self.format_var.get(), "quality": self.quality_var.get(), "delay": self.delay_var.get(), "source": self.source_var.get(), "cpu_profile": self.cpu_profile_var.get(), "max_mins": self.max_mins_var.get(), "max_secs": self.max_secs_var.get(), "block_words": self.block_words_var.get(), "cookie_path": self.cookie_path_var.get(), "setup_complete": True}
        try: SETTINGS_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except Exception: pass

    def on_close(self):
        self.save_settings()
        if self._busy() and not self._closing:
            # Ask the batch to stop and wait for it (kills ffmpeg, saves state). Network calls can block up
            # to ~30s, so allow 45s. Clicking X a second time force-closes immediately.
            self._closing = True
            backend.STOP_EVENT.set(); backend.CONVERT_STOP.set()
            self.title("Music Downloader - stopping... (click X again to force close)")
            self.stop_btn.configure(state="disabled", text="ABORTING...")
            self.after(200, self._wait_and_close, time.monotonic() + 45)
            return
        self.destroy()

    def _busy(self):
        return any(t is not None and t.is_alive() for t in (self.worker_thread, self.qc_thread))

    def _wait_and_close(self, deadline):
        if self._busy() and time.monotonic() < deadline:
            self.after(200, self._wait_and_close, deadline)
        else:
            self.destroy()

    def poll_gui_queue(self):
        try:
            for _ in range(200):   # at most 200 updates per tick, so a flood of messages can't freeze the window
                try: cmd = self.gui_queue.get_nowait()
                except queue.Empty: break
                try:
                    if cmd[0] == "log": self._do_log(cmd[1])
                    elif cmd[0] == "overall": self._do_update_overall(cmd[1], cmd[2], cmd[3])
                    elif cmd[0] == "track_start": self._do_track_start(cmd[1], cmd[2], cmd[3], cmd[4])
                    elif cmd[0] == "track_end": self._do_track_end(cmd[1], cmd[2], cmd[3])
                    elif cmd[0] == "dl_bar": self._do_update_dl_bar(cmd[1], cmd[2])
                    elif cmd[0] == "cv_start": self._do_update_cv_start(cmd[1], cmd[2])
                    elif cmd[0] == "cv_end": self._do_update_cv_end(cmd[1])
                    elif cmd[0] == "reset": self._do_reset_ui()
                except Exception:
                    pass   # one bad update (e.g. a widget that was just closed) must never stop the polling loop
        finally:
            self.after(100, self.poll_gui_queue)

    def update_risk_label(self, *args):
        if not hasattr(self, 'risk_label_adv'): return
        try:
            val = float(self.delay_var.get())
            if val < 0.7: self.risk_label_adv.configure(text="High Risk (<0.7s)", text_color=STOP_RED)
            elif val < 1.0: self.risk_label_adv.configure(text="High Risk (<1.0s)", text_color=STOP_RED)
            elif val < 2.0: self.risk_label_adv.configure(text="Mod Risk (<2.0s)", text_color="#FFA500")
            else: self.risk_label_adv.configure(text="Low Risk", text_color=ACCENT)
        except (ValueError, tk.TclError): pass   # TclError: the settings panel was closed, its label no longer exists

    def trigger_risk_popup(self):
        try:
            if float(self.delay_var.get()) < 0.7 and not self.risk_popup_open:
                self.risk_popup_open = True
                popup = RiskPopup(self, self.delay_var, lambda: self.delay_var.set("3.0"))
                popup.bind("<Destroy>", lambda e: setattr(self, "risk_popup_open", False))
                return True
        except ValueError: pass
        return False

    def build_minimal_ui(self):
        self.main_container = ctk.CTkFrame(self, fg_color=BG_MAIN); self.main_container.pack(fill="both", expand=True, padx=25, pady=25)
        csv_frame = ctk.CTkFrame(self.main_container, fg_color="transparent"); csv_frame.pack(fill="x", pady=(0, 4))
        self.input_seg = ctk.CTkSegmentedButton(csv_frame, values=["Spotify", "YouTube"], variable=self.input_type_var, command=self.on_input_type_change, width=150, corner_radius=0, border_width=2, selected_color=SEG_SELECTED, selected_hover_color=SEG_SELECTED, unselected_color=BG_MAIN, unselected_hover_color=BORDER, fg_color=BORDER, text_color=TEXT_MAIN, font=self.native_font)
        self.input_seg.pack(side="left", padx=(0, 10))
        self.csv_entry = ctk.CTkEntry(csv_frame, textvariable=self.csv_path, font=self.native_font, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_CARD, text_color=TEXT_MAIN)
        self.csv_entry.pack(side="left", fill="x", expand=True, ipady=4)
        btn_browse = ctk.CTkButton(csv_frame, text="Browse", width=80, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.browse_csv)
        btn_browse.pack(side="right", padx=(10, 0), ipady=4)
        ToolTip(btn_browse, "Choose a file (a CSV, or a text file of links for YouTube).")
        self.input_hint = ctk.CTkLabel(self.main_container, text="", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_DIM); self.input_hint.pack(anchor="w", pady=(0, 14))
        self._refresh_input_ui()
        out_frame = ctk.CTkFrame(self.main_container, fg_color="transparent"); out_frame.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(out_frame, text="Save to:", font=self.native_font, text_color=TEXT_MAIN, width=70, anchor="w").pack(side="left", padx=(0, 10))
        self.out_entry = ctk.CTkEntry(out_frame, textvariable=self.out_path, font=self.native_font, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_CARD, text_color=TEXT_MAIN)
        self.out_entry.pack(side="left", fill="x", expand=True, ipady=4)
        btn_out = ctk.CTkButton(out_frame, text="Browse", width=80, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.browse_out)
        btn_out.pack(side="right", padx=(10, 0), ipady=4)
        ToolTip(btn_out, "Choose the folder your songs are saved to.")
        prog_frame = ctk.CTkFrame(self.main_container, fg_color="transparent"); prog_frame.pack(fill="x", pady=(0, 20))
        self.overall_label = ctk.CTkLabel(prog_frame, text="Ready", font=self.bold_font, text_color=TEXT_MAIN); self.overall_label.pack(anchor="w")
        self.overall_bar = ctk.CTkProgressBar(prog_frame, height=18, corner_radius=0, progress_color=PROGRESS_FG, fg_color=PROGRESS_BG)
        self.overall_bar.pack(fill="x", pady=(5, 5)); self.overall_bar.set(0)
        ToolTip(self.overall_bar, "Overall progress of the current download batch.")
        self.status_lbl = ctk.CTkLabel(prog_frame, text="Idle", font=self.native_font, text_color=TEXT_DIM); self.status_lbl.pack(anchor="w")
        btn_frame = ctk.CTkFrame(self.main_container, fg_color="transparent"); btn_frame.pack(fill="x", side="bottom"); btn_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.start_btn = ctk.CTkButton(btn_frame, text="START", font=self.bold_font, height=40, corner_radius=0, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self.start_download)
        self.start_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        ToolTip(self.start_btn, "Begin downloading the queue.")
        self.stop_btn = ctk.CTkButton(btn_frame, text="STOP", font=self.bold_font, height=40, corner_radius=0, fg_color=STOP_RED, hover_color=STOP_HOVER, text_color="white", state="disabled", command=self.stop_download)
        self.stop_btn.grid(row=0, column=1, padx=5, sticky="ew")
        ToolTip(self.stop_btn, "Safely stop the queue. Progress is saved.")
        self.retry_btn = ctk.CTkButton(btn_frame, text="RETRY", font=self.bold_font, height=40, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.retry_failed)
        self.retry_btn.grid(row=0, column=2, padx=5, sticky="ew")
        ToolTip(self.retry_btn, "Retry tracks that failed previously.")
        self.conv_btn = ctk.CTkButton(btn_frame, text="Convert", font=self.bold_font, height=40, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.open_converter)
        self.conv_btn.grid(row=0, column=3, padx=5, sticky="ew")
        ToolTip(self.conv_btn, "Quick Converter: turn any YouTube link into MP3, M4A or Opus.")
        self.adv_btn = ctk.CTkButton(btn_frame, text="Settings", font=self.bold_font, width=90, height=40, corner_radius=0, fg_color=BG_CARD, text_color=TEXT_MAIN, hover_color=BORDER, border_width=1, border_color=BORDER, command=self.toggle_advanced)
        self.adv_btn.grid(row=0, column=4, padx=(5, 0), sticky="e")
        ToolTip(self.adv_btn, "Open advanced settings and worker panel.")

    def toggle_advanced(self):
        if not self.is_advanced:
            self.is_advanced = True; self.geometry("850x700"); self.minsize(800, 650)
            self.adv_btn.configure(text="Close", fg_color=STOP_RED, border_color=STOP_RED, hover_color=STOP_HOVER)
            self.adv_container = ctk.CTkFrame(self, fg_color="transparent")
            self.adv_container.pack(fill="both", expand=True, padx=25, pady=(0, 25))
            self.after(50, self.build_advanced_ui) 
        else:
            self.is_advanced = False; self.adv_container.pack_forget(); self.adv_container.destroy()
            self.log_text = None; self.dl_bars = {}; self.cv_bars = {}   # those widgets no longer exist
            self.minsize(1, 1); self.geometry("640x420"); self.resizable(False, False)
            self.adv_btn.configure(text="Settings", fg_color=BG_CARD, border_color=BORDER, hover_color=BORDER)

    def build_advanced_ui(self):
        self.adv_container.grid_columnconfigure(0, weight=1); self.adv_container.grid_columnconfigure(1, weight=0); self.adv_container.grid_rowconfigure(1, weight=1)
        set_frame = ctk.CTkFrame(self.adv_container, corner_radius=0, fg_color=BG_CARD, border_width=1, border_color=BORDER)
        set_frame.grid(row=0, column=0, padx=(0, 15), pady=(0, 15), sticky="ew"); set_frame.grid_columnconfigure((1, 3), weight=1)
        pad_opts = {"padx": 15, "pady": 10}
        
        lbl_mode = ctk.CTkLabel(set_frame, text="Mode:", font=self.native_font, text_color=TEXT_MAIN); lbl_mode.grid(row=0, column=0, sticky="w", **pad_opts)
        ToolTip(lbl_mode, "Full Artist Catalog: Downloads entire discography.\nTop 10 Songs: Only their most popular hits.\nPlaylist Only: Only the songs in your CSV.")
        self.mode_menu = ctk.CTkOptionMenu(set_frame, values=["Full Artist Catalog", "Top 10 Songs", "Playlist Tracks Only", "Original Albums"], variable=self.download_mode, font=self.native_font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN)
        self.mode_menu.grid(row=0, column=1, sticky="w", padx=5, pady=10); self._refresh_input_ui()
        
        lbl_fmt = ctk.CTkLabel(set_frame, text="Format:", font=self.native_font, text_color=TEXT_MAIN); lbl_fmt.grid(row=0, column=2, sticky="w", **pad_opts)
        ToolTip(lbl_fmt, "M4A is best for Apple/Android.\nOpus is smallest size (great for Pixel).\nMP3 is most compatible.")
        ctk.CTkOptionMenu(set_frame, values=["M4A (AAC)", "MP3", "Opus"], variable=self.format_var, font=self.native_font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN).grid(row=0, column=3, sticky="w", padx=5, pady=10)

        lbl_quality = ctk.CTkLabel(set_frame, text="Quality:", font=self.native_font, text_color=TEXT_MAIN); lbl_quality.grid(row=1, column=0, sticky="w", **pad_opts)
        ToolTip(lbl_quality, "192kbps is CD quality. Higher numbers mean larger files\nbut no audible difference.")
        ctk.CTkOptionMenu(set_frame, values=["128", "192", "256", "320"], variable=self.quality_var, font=self.native_font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN).grid(row=1, column=1, sticky="w", padx=5, pady=10)
        
        lbl_source = ctk.CTkLabel(set_frame, text="Source:", font=self.native_font, text_color=TEXT_MAIN); lbl_source.grid(row=1, column=2, sticky="w", **pad_opts)
        ToolTip(lbl_source, "YouTube has better quality for mainstream.\nSoundCloud is better for EDM/underground.")
        ctk.CTkOptionMenu(set_frame, values=["YouTube", "SoundCloud"], variable=self.source_var, font=self.native_font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN).grid(row=1, column=3, sticky="w", padx=5, pady=10)

        lbl_delay = ctk.CTkLabel(set_frame, text="Delay (s):", font=self.native_font, text_color=TEXT_MAIN); lbl_delay.grid(row=2, column=0, sticky="w", **pad_opts)
        ToolTip(lbl_delay, "Time between downloads. Lower = faster but high risk of IP ban.")
        delay_entry_frame = ctk.CTkFrame(set_frame, fg_color="transparent"); delay_entry_frame.grid(row=2, column=1, sticky="w", padx=5, pady=10)
        delay_entry = ctk.CTkEntry(delay_entry_frame, textvariable=self.delay_var, width=60, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_MAIN, text_color=TEXT_MAIN)
        delay_entry.pack(side="left")
        delay_entry.bind("<FocusOut>", lambda e: self.trigger_risk_popup())
        delay_entry.bind("<Return>", lambda e: self.trigger_risk_popup())
        self.risk_label_adv = ctk.CTkLabel(delay_entry_frame, text="", font=ctk.CTkFont(size=11)); self.risk_label_adv.pack(side="left", padx=10); self.update_risk_label()
        
        lbl_cpu = ctk.CTkLabel(set_frame, text="CPU Profile:", font=self.native_font, text_color=TEXT_MAIN); lbl_cpu.grid(row=2, column=2, sticky="w", **pad_opts)
        ToolTip(lbl_cpu, "Smart Auto pauses downloads if your CPU gets busy.\nMax speed ignores CPU limits.")
        ctk.CTkOptionMenu(set_frame, values=["Smart Auto", "Low Impact", "Max Speed"], variable=self.cpu_profile_var, font=self.native_font, corner_radius=0, fg_color=BG_MAIN, button_color=BORDER, text_color=TEXT_MAIN).grid(row=2, column=3, sticky="w", padx=5, pady=10)

        lbl_maxlen = ctk.CTkLabel(set_frame, text="Max Length:", font=self.native_font, text_color=TEXT_MAIN); lbl_maxlen.grid(row=3, column=0, sticky="w", **pad_opts)
        ToolTip(lbl_maxlen, "Skips any song longer than this limit\n(e.g., to avoid podcasts or DJ sets).")
        dur_frame = ctk.CTkFrame(set_frame, fg_color="transparent"); dur_frame.grid(row=3, column=1, sticky="w", padx=5, pady=10)
        ctk.CTkEntry(dur_frame, textvariable=self.max_mins_var, width=40, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_MAIN, text_color=TEXT_MAIN).pack(side="left")
        ctk.CTkLabel(dur_frame, text="m", font=self.native_font, text_color=TEXT_MAIN).pack(side="left", padx=(2,5))
        ctk.CTkEntry(dur_frame, textvariable=self.max_secs_var, width=40, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_MAIN, text_color=TEXT_MAIN).pack(side="left")
        ctk.CTkLabel(dur_frame, text="s", font=self.native_font, text_color=TEXT_MAIN).pack(side="left", padx=(2,0))

        lbl_cook = ctk.CTkLabel(set_frame, text="Cookies:", font=self.native_font, text_color=TEXT_MAIN); lbl_cook.grid(row=3, column=2, sticky="w", **pad_opts)
        ToolTip(lbl_cook, "Reload your YouTube cookies if you start getting bot errors.")
        ctk.CTkButton(set_frame, text="Reload Cookies", corner_radius=0, fg_color=BG_MAIN, border_width=1, border_color=BORDER, text_color=TEXT_MAIN, hover_color=BORDER, command=self.load_cookie_file).grid(row=3, column=3, sticky="w", padx=5, pady=10)

        lbl_block = ctk.CTkLabel(set_frame, text="Block Words:", font=self.native_font, text_color=TEXT_MAIN); lbl_block.grid(row=4, column=0, sticky="w", **pad_opts)
        ToolTip(lbl_block, "Any song title containing these words will be skipped.\nSeparate with commas (e.g., live, remix, sped up).")
        ctk.CTkEntry(set_frame, textvariable=self.block_words_var, corner_radius=0, border_width=1, border_color=BORDER, fg_color=BG_MAIN, text_color=TEXT_MAIN).grid(row=4, column=1, columnspan=3, sticky="ew", padx=5, pady=10)

        log_frame = ctk.CTkFrame(self.adv_container, corner_radius=0, fg_color=BG_CARD, border_width=1, border_color=BORDER)
        log_frame.grid(row=1, column=0, padx=(0, 15), pady=0, sticky="nsew"); log_frame.grid_rowconfigure(0, weight=1); log_frame.grid_columnconfigure(0, weight=1)
        self.log_text = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=11), corner_radius=0, fg_color=BG_MAIN, text_color=ACCENT, border_width=0)
        self.log_text.grid(row=0, column=0, padx=10, pady=10, sticky="nsew"); self.log_text.insert("end", "".join(self.log_buffer) or "> System Ready...\n"); self.log_text.see("end"); self.log_text.configure(state="disabled")

        right_frame = ctk.CTkFrame(self.adv_container, corner_radius=0, fg_color=BG_CARD, border_width=1, border_color=BORDER, width=220)
        right_frame.grid(row=0, column=1, rowspan=2, sticky="nsew"); right_frame.grid_propagate(False)
        ctk.CTkLabel(right_frame, text="Workers", font=self.bold_font, text_color=TEXT_MAIN).pack(pady=(15, 10))
        self.eta_label = ctk.CTkLabel(right_frame, text="ETA: --", font=self.native_font, text_color=TEXT_DIM); self.eta_label.pack(pady=(0, 15))

        self.dl_bars = {}; self.cv_bars = {}
        def create_worker_bar(parent, title, key, is_downloader, tip_text):
            f = ctk.CTkFrame(parent, fg_color="transparent"); f.pack(fill="x", pady=6, padx=15)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=TEXT_MAIN, width=35, anchor="w").pack(side="left")
            bar = ctk.CTkProgressBar(f, height=10, corner_radius=0, progress_color=ACCENT if is_downloader else TEXT_DIM, fg_color=BG_MAIN)
            bar.pack(side="left", fill="x", expand=True, padx=5); bar.set(0)
            ToolTip(bar, tip_text)
            if is_downloader: self.dl_bars[key] = bar
            else: self.cv_bars[key] = bar

        create_worker_bar(right_frame, "DL 1", "DL-1", True, "Download Thread 1")
        create_worker_bar(right_frame, "DL 2", "DL-2", True, "Download Thread 2")
        create_worker_bar(right_frame, "DL 3", "DL-3", True, "Download Thread 3")
        create_worker_bar(right_frame, "CV 1", "CV-1", False, "Formatting Thread 1")
        create_worker_bar(right_frame, "CV 2", "CV-2", False, "Formatting Thread 2")

    def browse_csv(self):
        types = [("CSV or text files", "*.csv *.txt"), ("All files", "*.*")] if self.input_type_var.get() == "YouTube" else [("CSV files", "*.csv")]
        filename = filedialog.askopenfilename(filetypes=types)
        if filename: self.csv_path.set(filename)

    def browse_out(self):
        folder = filedialog.askdirectory(initialdir=self.out_path.get() if Path(self.out_path.get() or ".").is_dir() else None)
        if folder: self.out_path.set(folder); self.save_settings()

    def load_cookie_file(self):
        filename = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if filename: self.cookie_path_var.set(filename); self.save_settings(); self.gui_queue.put(("log", "Cookie file loaded."))

    def _do_log(self, message):
        self.log_buffer.append(f"> {message}\n")
        if not self.is_advanced or getattr(self, "log_text", None) is None: return
        self.log_text.configure(state="normal"); self.log_text.insert("end", f"> {message}\n"); self.log_text.see("end")
        try:
            if int(self.log_text.index("end-1c").split(".")[0]) > 1000:
                self.log_text.delete("1.0", "500.0")
        except Exception: pass
        self.log_text.configure(state="disabled")

    def _do_update_dl_bar(self, thread_name, pct):
        if self.is_advanced and thread_name in self.dl_bars: self.dl_bars[thread_name].set(pct)

    def _do_update_cv_start(self, thread_name, title):
        if self.is_advanced and thread_name in self.cv_bars: self.cv_bars[thread_name].set(0.5) 

    def _do_update_cv_end(self, thread_name):
        if self.is_advanced and thread_name in self.cv_bars: self.cv_bars[thread_name].set(1)

    def _do_update_overall(self, current, total, msg):
        if total > 0: 
            self.overall_bar.set(current / total); self.overall_label.configure(text=f"{current} / {total} Tracks")
        else: self.overall_label.configure(text=f"{msg}")
        # The UI is only re-enabled by the "reset" event the worker thread posts when it has fully finished.
        if msg == "Batch Complete!": self._do_log("Batch complete.")

    def _do_reset_ui(self):
        self.is_running = False; self.start_btn.configure(state="normal", text="START", fg_color=ACCENT, text_color=ON_ACCENT)
        self.stop_btn.configure(state="disabled", text="STOP", fg_color=STOP_RED); self.retry_btn.configure(state="normal")
        if self.is_advanced:
            for b in list(self.dl_bars.values()) + list(self.cv_bars.values()): b.set(0)

    def _do_track_start(self, artist, title, completed, total):
        self._do_update_overall(completed, total, "")
        self.status_lbl.configure(text=f"Downloading: {title}")

    def _do_track_end(self, completed, total, eta_str):
        self._do_update_overall(completed, total, "")
        if self.is_advanced: self.eta_label.configure(text=f"ETA: {eta_str}")

    def stop_download(self):
        if self.is_running: 
            backend.STOP_EVENT.set()
            self.gui_queue.put(("log", "ABORT command received. Terminating..."))
            self.stop_btn.configure(state="disabled", text="ABORTING...")

    def start_download(self, retry_only=False):
        if self.is_running: return
        if self.qc_running: messagebox.showinfo("Busy", "The Quick Converter is running. Wait for it or stop it first."); return
        if not retry_only:
            err = self._validate_input()
            if err: messagebox.showerror("Error", err); return
        input_type = "youtube" if self.input_type_var.get() == "YouTube" else "spotify"
        source_text = self.csv_path.get().strip().strip('"')
        
        # Check delay risk on start too
        try:
            if float(self.delay_var.get()) < 0.7 and not retry_only:
                if self.trigger_risk_popup(): return 
        except ValueError: pass

        self.save_settings(); self.is_running = True
        backend.STOP_EVENT.clear()  # reset BEFORE the worker starts, so an early STOP can't be erased
        self.start_btn.configure(state="disabled", text="RUNNING...", fg_color=BG_CARD, text_color=TEXT_MAIN)
        self.stop_btn.configure(state="normal", text="STOP", fg_color=STOP_RED)
        self.retry_btn.configure(state="disabled")
        
        if not retry_only: self.gui_queue.put(("log", "System executing batch..."))
        try:
            max_dur = (int(self.max_mins_var.get() or 0) * 60) + int(self.max_secs_var.get() or 0)
            mode_map = {"Full Artist Catalog": "full_artist", "Top 10 Songs": "top10", "Playlist Tracks Only": "playlist", "Original Albums": "album"}
            profile_map = {"Smart Auto": "smart", "Low Impact": "low", "Max Speed": "max"}
            fmt_str = "m4a" if "M4A" in self.format_var.get() else ("opus" if "Opus" in self.format_var.get() else "mp3")
            
            thread = threading.Thread(target=self.run_thread, args=(int(self.limit_var.get()), float(self.delay_var.get()), max_dur, mode_map.get(self.download_mode.get(), "full_artist"), profile_map.get(self.cpu_profile_var.get(), "smart"), fmt_str, self.block_words_var.get(), retry_only, input_type, source_text))
            thread.daemon = True; self.worker_thread = thread; thread.start()
        except ValueError: messagebox.showerror("Error", "Settings must be valid numbers."); self.gui_queue.put(("reset",))

    def retry_failed(self): self.start_download(retry_only=True)

    def run_thread(self, limit, delay, max_dur, mode, profile, fmt, block_words, retry_only, input_type="spotify", source_text=""):
        try:
            import spotify_to_mp3
            def yt_hook(d, t_name):
                if time.time() - self.last_update_time < 0.1: return
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate')
                    if total and d.get('downloaded_bytes'):
                        self.last_update_time = time.time()
                        pct = d['downloaded_bytes'] / total
                        self.gui_queue.put(("dl_bar", t_name, pct))

            spotify_to_mp3.PROGRESS_CALLBACK = yt_hook
            
            backend.run_gui_batch(
                csv_path=source_text, out_dir=self.out_path.get(), limit=limit, quality=self.quality_var.get(), delay=delay,
                update_ui_cb=lambda c, t, m: self.gui_queue.put(("overall", c, t, m)), 
                track_start_cb=lambda a, ti, c, t: self.gui_queue.put(("track_start", a, ti, c, t)), 
                track_end_cb=lambda c, t, e: self.gui_queue.put(("track_end", c, t, e)), 
                log_cb=lambda msg: self.gui_queue.put(("log", msg)), 
                cpu_threads=max(2, (psutil.cpu_count(logical=False) or 4) // 2),
                retry_only=retry_only, primary_source=self.source_var.get().lower(), max_duration=max_dur, download_mode=mode, cpu_profile=profile,
                output_format=fmt, block_words=block_words, cookie_path=self.cookie_path_var.get(), input_type=input_type,
                convert_start_cb=lambda tn, ti: self.gui_queue.put(("cv_start", tn, ti)), 
                convert_end_cb=lambda tn: self.gui_queue.put(("cv_end", tn))
            )
        except Exception as e:
            msg = f"ERROR: {e}"
            self.gui_queue.put(("log", msg))
        finally:
            self.gui_queue.put(("reset",))

if __name__ == "__main__":
    app = MusicApp(); app.mainloop()
