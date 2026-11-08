#!/usr/bin/env python3
"""Tampilan desktop untuk Batch Downloader (Google Drive, Dropbox, OneDrive, MediaFire, MEGA, link langsung)."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from types import SimpleNamespace

# Aplikasi berjendela (.exe tanpa konsol) tidak punya stdout/stderr → arahkan ke devnull sebelum modul lain dimuat
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

IS_MAC, IS_WIN = sys.platform == "darwin", sys.platform.startswith("win")
FROZEN = getattr(sys, "frozen", False)            # True kalau dijalankan sebagai .exe (PyInstaller)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE))
FM = "Finder" if IS_MAC else "Explorer" if IS_WIN else "File Manager"
MONO = "Menlo" if IS_MAC else "Consolas" if IS_WIN else "DejaVu Sans Mono"
UI = "Helvetica Neue" if IS_MAC else "Segoe UI" if IS_WIN else "DejaVu Sans"
import downloader as D  # noqa: E402

APP = "Batch Downloader"
if IS_MAC:
    CFG = Path.home() / "Library" / "Application Support" / "BatchDownloader" / "settings.json"
elif IS_WIN:
    CFG = Path(os.environ.get("APPDATA", Path.home())) / "BatchDownloader" / "settings.json"
else:
    CFG = Path.home() / ".config" / "BatchDownloader" / "settings.json"
DEFAULT_OUT = str(Path.home() / "Downloads" / "BatchDownloader")
PLACEHOLDER = ("Tempel link di sini — satu link per baris.\n\n"
               "Contoh:\n"
               "https://drive.google.com/drive/folders/XXXX\n"
               "https://www.dropbox.com/scl/fo/XXXX?rlkey=XXXX | Mockup\n"
               "https://www.youtube.com/watch?v=XXXX   (juga Instagram, Facebook, TikTok, X…)\n\n"
               "Tambahkan  | nama subfolder  di belakang link kalau ingin hasilnya masuk subfolder tertentu.")


def load_cfg() -> dict:
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cfg(data: dict):
    try:
        CFG.parent.mkdir(parents=True, exist_ok=True)
        CFG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def open_in_finder(path: str, create: bool = True):
    """Buka folder di Finder (folder dibuat dulu kalau belum ada)."""
    p = Path(path).expanduser()
    if not p.exists() and create:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
    if p.exists():
        _open(p)


def _open(p: Path):
    if IS_WIN:
        os.startfile(str(p))  # noqa: S606
    elif IS_MAC:
        subprocess.run(["open", str(p)])
    else:
        subprocess.run(["xdg-open", str(p)])


def reveal_in_finder(path: Path):
    """Buka Finder dan sorot file/folder tersebut."""
    if path.exists():
        if IS_MAC:
            subprocess.run(["open", "-R", str(path)])
        elif IS_WIN:
            subprocess.run(["explorer", "/select,", str(path)])
        else:
            _open(path.parent)
    elif path.parent.exists():
        _open(path.parent)


def notify(title: str, msg: str):
    if sys.platform == "darwin":
        msg = msg.replace('"', "'")
        subprocess.Popen(["osascript", "-e", f'display notification "{msg}" with title "{title}"'])


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.placeholder_on = False
        cfg = load_cfg()

        root.title(APP)
        icon = BASE / "assets" / "icon_256.png"
        self.icon_img = self.logo_img = None
        if icon.exists():
            try:
                self.icon_img = tk.PhotoImage(file=str(icon))
                root.iconphoto(True, self.icon_img)          # ikon Dock & jendela
                self.logo_img = self.icon_img.subsample(4)   # 64 px untuk judul
            except tk.TclError:
                pass
        # sesuaikan dengan layar (laptop 768px pun muat)
        sh = root.winfo_screenheight()
        self.small = sh < 900
        h = min(880, sh - (130 if self.small else 90))       # sisakan ruang untuk taskbar/Dock
        x = max(0, (root.winfo_screenwidth() - 940) // 2)
        root.geometry(f"940x{h}+{x}+{0 if self.small else 40}")
        root.minsize(760, min(600, sh - 90))

        self.v_out = tk.StringVar(value=cfg.get("out", DEFAULT_OUT))
        self.v_workers = tk.IntVar(value=cfg.get("workers", 3))
        self.v_retries = tk.IntVar(value=cfg.get("retries", 6))
        self.v_keepzip = tk.BooleanVar(value=cfg.get("keep_zip", False))
        self.v_autoopen = tk.BooleanVar(value=cfg.get("auto_open", True))
        self.q_values = list(D.QUALITY_LABELS.values())
        self.v_quality = tk.StringVar(value=D.QUALITY_LABELS.get(cfg.get("video_quality", "best"), "Terbaik"))
        self.browsers = {"Tidak pakai": None, "Chrome": "chrome", "Safari": "safari", "Firefox": "firefox",
                         "Edge": "edge", "Brave": "brave", "Opera": "opera", "Vivaldi": "vivaldi"}
        self.v_browser = tk.StringVar(value=next((k for k, v in self.browsers.items()
                                                   if v == cfg.get("cookies_browser")), "Tidak pakai"))
        self.v_vidmsg = tk.StringVar(value="")
        self.last_out: Path | None = None
        # API key disimpan di Keychain macOS (bukan file teks). Pindahkan key lama dari settings.json kalau ada.
        old = cfg.pop("api_key", "")
        if old and D.keychain_set(old):
            save_cfg(cfg)
        self.v_apikey = tk.StringVar(value=D.keychain_get() or old or "")
        self.v_keymsg = tk.StringVar(value="✓ Tersimpan aman" if self.v_apikey.get() else "")
        self._key_job = None
        self.v_apikey.trace_add("write", lambda *_: self._schedule_key_save())
        self.v_cookies = tk.StringVar(value=cfg.get("cookies", ""))
        self.v_count = tk.StringVar(value="0 link")
        self.v_status = tk.StringVar(value="Siap.")
        self.v_active = tk.StringVar(value="")

        outer = ttk.Frame(root, padding=(18, 14, 18, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        # ── judul ──
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        if self.logo_img:
            ttk.Label(head, image=self.logo_img).pack(side="left", padx=(0, 12))
        htxt = ttk.Frame(head)
        htxt.pack(side="left", anchor="w")
        ttk.Label(htxt, text=APP, font=(UI, 22, "bold")).pack(anchor="w")
        ttk.Label(htxt, text="Google Drive · Dropbox · OneDrive · MediaFire · MEGA · link langsung — "
                             "struktur folder ikut disalin", foreground="#6b6b6b").pack(anchor="w")

        # ── 1. link ──
        box1 = ttk.LabelFrame(outer, text="  1. Link download  ", padding=10)
        box1.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        box1.columnconfigure(0, weight=1)
        box1.rowconfigure(0, weight=1)
        self.links = tk.Text(box1, height=7, wrap="none", font=(MONO, 12), undo=True,
                             relief="solid", borderwidth=1, padx=8, pady=6)
        self.links.grid(row=0, column=0, sticky="nsew")
        self.fg = self.links.cget("foreground")
        sb = ttk.Scrollbar(box1, orient="vertical", command=self.links.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.links.configure(yscrollcommand=sb.set)
        self.links.bind("<FocusIn>", lambda e: self._ph_clear())
        self.links.bind("<FocusOut>", lambda e: self._ph_show())
        self.links.bind("<<Modified>>", self._on_links_changed)

        bar1 = ttk.Frame(box1)
        bar1.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bar1, text="Tempel dari Clipboard", command=self.paste).pack(side="left")
        ttk.Button(bar1, text="Impor dari file .txt…", command=self.import_txt).pack(side="left", padx=6)
        ttk.Button(bar1, text="Bersihkan", command=self.clear_links).pack(side="left")
        ttk.Label(bar1, textvariable=self.v_count, foreground="#6b6b6b").pack(side="right")

        # ── 2. folder ──
        box2 = ttk.LabelFrame(outer, text="  2. Simpan ke folder  ", padding=10)
        box2.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        box2.columnconfigure(0, weight=1)
        ttk.Entry(box2, textvariable=self.v_out).grid(row=0, column=0, sticky="ew")
        ttk.Button(box2, text="Pilih Folder…", command=self.pick_out).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(box2, text=f"📂 Buka di {FM}", command=lambda: open_in_finder(self.v_out.get())
                   ).grid(row=0, column=2, padx=(6, 0))
        ttk.Checkbutton(box2, text=f"Buka {FM} otomatis setelah download selesai", variable=self.v_autoopen
                        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # ── 3. pengaturan ──
        box3 = ttk.LabelFrame(outer, text="  3. Pengaturan  ", padding=10)
        box3.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        box3.columnconfigure(4, weight=1)
        ttk.Label(box3, text="Download paralel").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(box3, from_=1, to=8, width=4, textvariable=self.v_workers).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(box3, text="Coba ulang per file").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(box3, from_=0, to=20, width=4, textvariable=self.v_retries).grid(row=0, column=3, padx=(6, 18))
        ttk.Checkbutton(box3, text="Simpan ZIP Dropbox setelah diekstrak", variable=self.v_keepzip
                        ).grid(row=0, column=4, sticky="w")

        ttk.Label(box3, text="Google API key (opsional)").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        kf = ttk.Frame(box3)
        kf.grid(row=1, column=2, columnspan=3, sticky="ew", pady=(8, 0))
        kf.columnconfigure(0, weight=1)
        self.key_entry = ttk.Entry(kf, textvariable=self.v_apikey, show="•")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self.v_showkey = tk.BooleanVar(value=False)
        ttk.Checkbutton(kf, text="Tampilkan", variable=self.v_showkey,
                        command=lambda: self.key_entry.configure(show="" if self.v_showkey.get() else "•")
                        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(kf, textvariable=self.v_keymsg, foreground="#1a7f37").grid(row=0, column=2, padx=(8, 0))

        ttk.Label(box3, text="cookies.txt Google (opsional)").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ck = ttk.Frame(box3)
        ck.grid(row=2, column=2, columnspan=3, sticky="ew", pady=(6, 0))
        ck.columnconfigure(0, weight=1)
        ttk.Entry(ck, textvariable=self.v_cookies).grid(row=0, column=0, sticky="ew")
        ttk.Button(ck, text="Pilih…", command=self.pick_cookies).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(box3, text="API key & cookies membantu saat kena kuota Google Drive. "
                             "Biarkan kosong kalau tidak punya.",
                  foreground="#8a8a8a").grid(row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # ── video: YouTube / Instagram / Facebook / TikTok / X / dll. (yt-dlp) ──
        ttk.Separator(box3).grid(row=4, column=0, columnspan=5, sticky="ew", pady=(10, 8))
        ttk.Label(box3, text="🎬 Kualitas video").grid(row=5, column=0, columnspan=2, sticky="w")
        vf = ttk.Frame(box3)
        vf.grid(row=5, column=2, columnspan=3, sticky="ew")
        ttk.Combobox(vf, textvariable=self.v_quality, values=self.q_values, state="readonly", width=16
                     ).pack(side="left")
        ttk.Label(vf, text="Login dari browser").pack(side="left", padx=(16, 6))
        ttk.Combobox(vf, textvariable=self.v_browser, values=list(self.browsers), state="readonly", width=12
                     ).pack(side="left")
        self.b_upd = ttk.Button(vf, text="Update mesin video", command=self.update_ytdlp)
        if not FROZEN:  # di versi .exe yt-dlp sudah dibundel; update = unduh versi aplikasi terbaru
            self.b_upd.pack(side="right")
        ttk.Label(box3, textvariable=self.v_vidmsg, foreground="#8a8a8a", wraplength=820, justify="left"
                  ).grid(row=6, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self._video_status()

        # opsi Google Drive (API key, cookies) bisa dilipat supaya muat di layar kecil
        self._gd_widgets = [w for r in (1, 2, 3) for w in box3.grid_slaves(row=r)]
        self.v_gdopts = tk.BooleanVar(value=not self.small)
        self.gd_toggle = ttk.Checkbutton(box3, variable=self.v_gdopts, command=self._toggle_gd)
        self.gd_toggle.grid(row=7, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self._toggle_gd()

        # ── tombol aksi ──
        act = ttk.Frame(outer)
        act.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        self.b_start = ttk.Button(act, text="⬇  Mulai Download", command=lambda: self.start(False),
                                  style="Accent.TButton")
        self.b_start.pack(side="left")
        self.b_check = ttk.Button(act, text="Cek Isi Link", command=lambda: self.start(True))
        self.b_check.pack(side="left", padx=6)
        self.b_stop = ttk.Button(act, text="Stop", command=self.stop, state="disabled")
        self.b_stop.pack(side="left")
        ttk.Button(act, text="📂 Buka Folder Hasil", command=lambda: open_in_finder(self.v_out.get())
                   ).pack(side="right")

        # ── progres ──
        prog = ttk.Frame(outer)
        prog.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        prog.columnconfigure(0, weight=1)
        self.pbar = ttk.Progressbar(prog, mode="determinate")
        self.pbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(prog, textvariable=self.v_status, font=(UI, 13, "bold")
                  ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(prog, textvariable=self.v_active, foreground="#6b6b6b"
                  ).grid(row=2, column=0, sticky="w")

        # ── log ──
        self.log = ScrolledText(outer, height=10, font=(MONO, 11), state="disabled",
                                relief="solid", borderwidth=1, padx=8, pady=6)
        self.log.grid(row=6, column=0, sticky="nsew", pady=(10, 0))
        self.log.tag_configure("ok", foreground="#1a7f37")
        self.log.tag_configure("fail", foreground="#c62828")
        self.log.tag_configure("wait", foreground="#b26a00")
        self.log.tag_configure("dim", foreground="#6b6b6b")
        self.log.bind("<Double-Button-1>", self._reveal_log_line)
        ttk.Label(outer, text=f"Tip: klik dua kali baris ✓ di log untuk menampilkan file itu di {FM}.",
                  foreground="#8a8a8a").grid(row=7, column=0, sticky="w", pady=(4, 0))
        outer.rowconfigure(1, weight=3, minsize=170)   # kotak link tidak boleh hilang
        outer.rowconfigure(6, weight=2, minsize=70)

        draft = cfg.get("links", "")
        if draft.strip():
            self.links.insert("1.0", draft)
        else:
            self._ph_show()
        self._update_count()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(150, self.poll)

    # ── placeholder di kotak link ──
    def _ph_show(self):
        if not self.links.get("1.0", "end").strip():
            self.placeholder_on = True
            self.links.insert("1.0", PLACEHOLDER)
            self.links.configure(foreground="#9a9a9a")
            self.links.edit_modified(False)

    def _ph_clear(self):
        if self.placeholder_on:
            self.placeholder_on = False
            self.links.delete("1.0", "end")
            self.links.configure(foreground=self.fg)

    def links_text(self) -> str:
        return "" if self.placeholder_on else self.links.get("1.0", "end")

    def _on_links_changed(self, _e=None):
        if self.links.edit_modified():
            self._update_count()
            self.links.edit_modified(False)

    def _update_count(self):
        n = len(D.parse_lines(self.links_text().splitlines()))
        self.v_count.set(f"{n} link valid")

    # ── aksi kecil ──
    def paste(self):
        try:
            txt = self.root.clipboard_get()
        except tk.TclError:
            return
        self._ph_clear()
        cur = self.links.get("1.0", "end").rstrip()
        self.links.delete("1.0", "end")
        self.links.insert("1.0", (cur + "\n" if cur else "") + txt.strip() + "\n")
        self._update_count()

    def import_txt(self):
        f = filedialog.askopenfilename(title="Pilih file daftar link", filetypes=[("Teks", "*.txt"), ("Semua", "*")])
        if f:
            self._ph_clear()
            self.links.insert("end", Path(f).read_text(encoding="utf-8", errors="ignore").strip() + "\n")
            self._update_count()

    def clear_links(self):
        self._ph_clear()
        self.links.delete("1.0", "end")
        self._update_count()
        self._ph_show()

    def pick_out(self):
        d = filedialog.askdirectory(title="Pilih folder tujuan download",
                                    initialdir=self.v_out.get() if Path(self.v_out.get()).exists() else str(Path.home()))
        if d:
            self.v_out.set(d)

    def pick_cookies(self):
        f = filedialog.askopenfilename(title="Pilih cookies.txt", filetypes=[("cookies.txt", "*.txt"), ("Semua", "*")])
        if f:
            self.v_cookies.set(f)

    def write_log(self, msg: str):
        tag = None
        if "✓" in msg or msg.startswith("\n✅"):
            tag = "ok"
        elif "✗" in msg or "⚠" in msg:
            tag = "fail"
        elif "⏳" in msg or "↪" in msg or "⏹" in msg:
            tag = "wait"
        elif "dilewati" in msg or "📁" in msg:
            tag = "dim"
        self.log.configure(state="normal")
        self.log.insert("end", msg.rstrip("\n") + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def persist(self):
        save_cfg({"out": self.v_out.get(), "workers": self._int(self.v_workers, 3),
                  "retries": self._int(self.v_retries, 6), "keep_zip": self.v_keepzip.get(),
                  "cookies": self.v_cookies.get().strip(), "auto_open": self.v_autoopen.get(),
                  "video_quality": self._quality_key(), "cookies_browser": self.browsers.get(self.v_browser.get()),
                  "links": self.links_text()})

    # ── API key: simpan otomatis ke Keychain setiap kali diubah ──
    def _schedule_key_save(self):
        if self._key_job:
            self.root.after_cancel(self._key_job)
        self.v_keymsg.set("menyimpan…")
        self._key_job = self.root.after(700, self._save_key)

    def _save_key(self):
        self._key_job = None
        key = self.v_apikey.get().strip()
        if D.keychain_set(key):
            self.v_keymsg.set("✓ Tersimpan aman" if key else "Key dihapus")
        else:
            self.v_keymsg.set("⚠ Gagal menyimpan")

    # ── video ──
    def _toggle_gd(self):
        show = self.v_gdopts.get()
        for w in self._gd_widgets:
            w.grid() if show else w.grid_remove()
        saved = " · 🔑 API key tersimpan" if self.v_apikey.get().strip() else ""
        self.gd_toggle.configure(text=("Sembunyikan" if show else "Tampilkan") +
                                 f" opsi Google Drive (API key, cookies){saved}")

    def _quality_key(self) -> str:
        return next((k for k, v in D.QUALITY_LABELS.items() if v == self.v_quality.get()), "best")

    def _video_status(self):
        if not D.ytdlp_available():
            self.v_vidmsg.set("⚠ Mesin video (yt-dlp) belum terpasang — klik “Update mesin video”.")
            return
        import yt_dlp
        hint = "winget install ffmpeg" if IS_WIN else "brew install ffmpeg"
        ff = "ffmpeg ✓" if D.FFMPEG else f"ffmpeg ✗ (kualitas tertinggi & MP3 butuh: {hint})"
        self.v_vidmsg.set(f"YouTube, Instagram, Facebook, TikTok, X, Vimeo & ±1800 situs · yt-dlp "
                          f"{yt_dlp.version.__version__} · {ff}. Playlist/channel jadi subfolder. "
                          f"IG/FB/video privat: pilih browser yang sudah login.")

    def update_ytdlp(self):
        self.b_upd.configure(state="disabled")
        self.v_vidmsg.set("Memperbarui yt-dlp…")

        def work():
            p = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-U", "--quiet", "yt-dlp"],
                               capture_output=True, text=True)
            if p.returncode != 0:
                p = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-U", "--quiet",
                                    "--break-system-packages", "yt-dlp"], capture_output=True, text=True)
            ok = p.returncode == 0
            self.root.after(0, lambda: self._after_update(ok, (p.stderr or "").strip()[-200:]))
        threading.Thread(target=work, daemon=True).start()

    def _after_update(self, ok: bool, err: str):
        self.b_upd.configure(state="normal")
        if ok:
            import importlib
            if "yt_dlp" in sys.modules:
                messagebox.showinfo(APP, "yt-dlp sudah diperbarui. Tutup & buka lagi aplikasi supaya versi baru dipakai.")
            importlib.invalidate_caches()
            self._video_status()
        else:
            self.v_vidmsg.set(f"⚠ Gagal update: {err}")

    @staticmethod
    def _int(var, default):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    # ── jalankan ──
    def start(self, dry: bool):
        if self.worker and self.worker.is_alive():
            return
        entries = D.parse_lines(self.links_text().splitlines())
        if not entries:
            messagebox.showwarning(APP, "Belum ada link yang valid.\nTempel link (diawali http/https), satu per baris.")
            return
        out = self.v_out.get().strip()
        if not out:
            messagebox.showwarning(APP, "Pilih folder tujuan dulu.")
            return
        cookies = self.v_cookies.get().strip()
        if cookies and not Path(cookies).expanduser().is_file():
            messagebox.showwarning(APP, f"File cookies tidak ditemukan:\n{cookies}")
            return
        self.persist()
        self.last_out = Path(out).expanduser().resolve()
        opts = SimpleNamespace(
            workers=max(1, min(8, self._int(self.v_workers, 3))), retries=max(0, self._int(self.v_retries, 6)),
            quota_wait=60.0, max_wait=900.0, gdrive_delay=1.0,
            gdrive_api_key=self.v_apikey.get().strip() or None,
            cookies=str(Path(cookies).expanduser()) if cookies else None,
            keep_zip=self.v_keepzip.get(), dry_run=dry,
            video_quality=self._quality_key(), cookies_browser=self.browsers.get(self.v_browser.get()))

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.pbar.configure(value=0, maximum=1)
        self.v_status.set("Membaca isi link…" if dry else "Membaca isi link & menyiapkan download…")
        self.v_active.set("")
        self._busy(True)
        self.dry = dry
        D.P.sink = self.q.put

        def work():
            try:
                res = D.run_batch(entries, Path(out), opts)
            except Exception as e:  # tampilkan error tak terduga di log, jangan crash diam-diam
                self.q.put(f"✗ Error: {e.__class__.__name__}: {e}")
                res = None
            self.q.put(("__done__", res))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        D.P.cancel.set()
        self.v_status.set("Menghentikan… (menunggu unduhan yang sedang berjalan berhenti)")
        self.b_stop.configure(state="disabled")

    def _busy(self, on: bool):
        self.b_start.configure(state="disabled" if on else "normal")
        self.b_check.configure(state="disabled" if on else "normal")
        self.b_stop.configure(state="normal" if on else "disabled")

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__done__":
                    self.finish(item[1])
                else:
                    self.write_log(str(item))
        except queue.Empty:
            pass
        if self.worker and self.worker.is_alive() and not getattr(self, "dry", False):
            s = D.P.snapshot()
            if s["total"]:
                fin = s["done"] + s["skipped"] + s["failed"]
                self.pbar.configure(maximum=s["total"], value=fin)
                extra = f" · {s['failed']} gagal" if s["failed"] else ""
                self.v_status.set(f"{fin} / {s['total']} file · {D.human(s['bytes'])} · "
                                  f"{D.human(s['speed'])}/s{extra}")
                act = s["active"]
                self.v_active.set(("Sedang diunduh: " + ", ".join(Path(a).name or a for a in act[:3])
                                   + (f" +{len(act) - 3}" if len(act) > 3 else "")) if act else "")
        self.root.after(250, self.poll)

    def finish(self, res):
        self._busy(False)
        self.v_active.set("")
        if not res:
            self.v_status.set("Berhenti karena error — lihat log.")
            return
        if res.get("dry_run"):
            self.v_status.set(f"Ditemukan {res['jobs']} file. Klik “Mulai Download” untuk mengunduh.")
            return
        self.pbar.configure(maximum=max(res["jobs"], 1), value=res["done"] + res["skipped"] + res["failed"])
        msg = (f"Selesai: {res['done']} diunduh · {res['skipped']} sudah ada · "
               f"{res['failed']} gagal · {D.human(res['bytes'])}")
        if res.get("cancelled"):
            msg = "Dihentikan. " + msg
        self.v_status.set(msg)
        notify(APP, msg)
        if self.v_autoopen.get() and (res["done"] or res["skipped"]) and self.last_out:
            open_in_finder(str(self.last_out), create=False)
        if res.get("failed_file") and not res.get("cancelled"):
            if messagebox.askyesno(APP, f"{res['failed']} file gagal (biasanya karena kuota Google Drive).\n\n"
                                        "Daftarnya disimpan di failed_links.txt. "
                                        "Muat daftar itu ke kotak link supaya bisa dicoba lagi nanti?"):
                self._ph_clear()
                self.links.delete("1.0", "end")
                self.links.insert("1.0", Path(res["failed_file"]).read_text(encoding="utf-8"))
                self._update_count()
                self.persist()

    def _reveal_log_line(self, event):
        """Klik dua kali baris log → tampilkan file/folder itu di Finder."""
        idx = self.log.index(f"@{event.x},{event.y}")
        line = self.log.get(f"{idx} linestart", f"{idx} lineend")
        base = self.last_out or Path(self.v_out.get()).expanduser()
        m = re.search(r"(?:✓|dilewati:|📁)\s+(.+?)(?:\s{2}\(\d+ item\))?$", line) or \
            re.search(r"✗\s+(.+?)\s+—", line)
        if m:
            rel = m.group(1).strip().split(" ← ")[0].rstrip("/")
            reveal_in_finder(base / rel)
        else:
            open_in_finder(str(base), create=False)
        return "break"

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP, "Download masih berjalan. Hentikan dan tutup?\n"
                                            "(Bisa dilanjutkan nanti — file setengah jalan disimpan.)"):
                return
            D.P.cancel.set()
        if self._key_job:
            self._save_key()
        self.persist()
        self.root.destroy()


def selftest(report: str):
    """Uji otomatis tanpa interaksi (dipakai CI): `BatchDownloader.exe --selftest hasil.json`.
    Hasil ditulis setelah setiap langkah, dan tiap langkah punya batas waktu, supaya macet pun tetap terlihat."""
    import tempfile
    import time
    rp = Path(report).resolve()
    res: dict = {"platform": sys.platform, "frozen": FROZEN, "python": sys.version.split()[0]}
    log: list[str] = []

    def save():
        res["log_tail"] = log[-30:]
        rp.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

    def step(name, fn, timeout):
        res[name] = "running"
        save()
        box = {}

        def run():
            try:
                box["v"] = fn()
            except Exception as e:  # noqa: BLE001
                box["v"] = f"ERROR {e!r}"
        t0 = time.time()
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(timeout)
        res[name] = box.get("v", f"TIMEOUT setelah {timeout}s")
        if isinstance(res[name], dict):
            res[name]["secs"] = round(time.time() - t0, 1)
        if th.is_alive():
            D.P.cancel.set()
        save()

    def gui_test():
        root = tk.Tk()
        App(root)
        root.update()
        root.destroy()
        return "ok"

    def key_test():
        ok = D.keychain_set("dummy", "selftest") and D.keychain_get("selftest") == "dummy"
        D.keychain_set("", "selftest")
        return "ok" if ok else "FAIL"

    res["gui"] = "running"   # Tk wajib di thread utama
    save()
    try:
        res["gui"] = gui_test()
    except Exception as e:  # noqa: BLE001
        res["gui"] = f"ERROR {e!r}"
    save()
    step("keystore", key_test, 30)
    res["ytdlp"] = D.ytdlp_available()
    res["ffmpeg"] = bool(D.FFMPEG)
    out = Path(tempfile.mkdtemp(prefix="bd-selftest-"))
    opts = SimpleNamespace(workers=3, retries=1, quota_wait=10.0, max_wait=30.0, gdrive_delay=1.0,
                           gdrive_api_key=None, cookies=None, keep_zip=False, dry_run=False,
                           video_quality="480", cookies_browser=None)
    D.P.sink = log.append
    tests = {
        "direct_link": "https://raw.githubusercontent.com/python/cpython/main/README.rst",
        "gdrive_folder": "https://drive.google.com/drive/folders/1KpLl_1tcK0eeehzN980zbG-3M2nhbVks",
        "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    }
    for name, url in tests.items():
        D.P.cancel.clear()

        def dl(url=url, name=name):
            r = D.run_batch([(url, Path(name))], out, opts)
            return {"done": r["done"], "failed": r["failed"]}
        step(name, dl, 120)
    res["files"] = sorted(str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*") if p.is_file())
    res["finished"] = True
    save()
    os._exit(0)  # jangan tunggu thread yang mungkin masih macet


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest.json")
        return
    root = tk.Tk()
    style = ttk.Style(root)
    for theme in ("aqua", "vista", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break
    App(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after(400, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
