#!/usr/bin/env python3
"""Tampilan desktop Batch Downloader (Google Drive, Dropbox, OneDrive, MediaFire, MEGA, video, link langsung)."""
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
from tkinter import filedialog, messagebox
from types import SimpleNamespace

# Aplikasi berjendela (.exe tanpa konsol) tidak punya stdout/stderr → arahkan ke devnull sebelum modul lain dimuat
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

import customtkinter as ctk  # noqa: E402

IS_MAC, IS_WIN = sys.platform == "darwin", sys.platform.startswith("win")
FROZEN = getattr(sys, "frozen", False)            # True kalau dijalankan sebagai .exe (PyInstaller)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE))
FM = "Finder" if IS_MAC else "Explorer" if IS_WIN else "File Manager"
MONO = "Menlo" if IS_MAC else "Consolas" if IS_WIN else "DejaVu Sans Mono"
import downloader as D  # noqa: E402

APP = "Batch Downloader"
if IS_MAC:
    CFG = Path.home() / "Library" / "Application Support" / "BatchDownloader" / "settings.json"
elif IS_WIN:
    CFG = Path(os.environ.get("APPDATA", Path.home())) / "BatchDownloader" / "settings.json"
else:
    CFG = Path.home() / ".config" / "BatchDownloader" / "settings.json"
DEFAULT_OUT = str(Path.home() / "Downloads" / "BatchDownloader")
PLACEHOLDER = ("Tempel link di sini, satu per baris.\n\n"
               "https://drive.google.com/drive/folders/XXXX\n"
               "https://www.dropbox.com/scl/fo/XXXX?rlkey=XXXX | Mockup\n"
               "https://www.youtube.com/watch?v=XXXX\n\n"
               "Tambahkan  | nama folder  di belakang link\n"
               "untuk menaruh hasilnya di subfolder.")

# ── palet (terang, gelap) ──
ACCENT = ("#1769E0", "#3B8BF5")
ACCENT_HOVER = ("#1257BD", "#2F76D6")
BG = ("#EEF1F5", "#111317")
CARD = ("#FFFFFF", "#1B1E24")
FIELD = ("#F6F8FA", "#14161B")
BORDER = ("#DDE2E8", "#2A2E36")
TEXT = ("#15181D", "#E8EAED")
MUTED = ("#5F6B7A", "#9AA3AF")
SUBTLE_BTN = ("#E6EAF0", "#262A32")
SUBTLE_HOVER = ("#D7DDE5", "#30353F")
DANGER = ("#D1242F", "#F0605A")
OK = "#2DA44E"
FAIL = "#E5534B"
WAIT = "#D29922"
DIM = "#8B949E"


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
    """Buka folder di Finder/Explorer (folder dibuat dulu kalau belum ada)."""
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
    """Buka Finder/Explorer dan sorot file/folder tersebut."""
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


# ───────────────────────── komponen kecil ─────────────────────────

def font(size=13, weight="normal", mono=False):
    return ctk.CTkFont(family=MONO, size=size) if mono else ctk.CTkFont(size=size, weight=weight)


def card(master, **kw):
    return ctk.CTkFrame(master, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER, **kw)


def section_title(master, text, hint=None):
    f = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(f, text=text, font=font(14, "bold"), text_color=TEXT).pack(side="left")
    if hint:
        ctk.CTkLabel(f, text=hint, font=font(12), text_color=MUTED).pack(side="left", padx=(8, 0))
    return f


def subtle_button(master, text, command, width=0, **kw):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=30, corner_radius=8,
                         fg_color=SUBTLE_BTN, hover_color=SUBTLE_HOVER, text_color=TEXT, font=font(13), **kw)


def option(master, var, values, width=150):
    return ctk.CTkOptionMenu(master, variable=var, values=values, width=width, height=30, corner_radius=8,
                             fg_color=SUBTLE_BTN, button_color=SUBTLE_BTN, button_hover_color=SUBTLE_HOVER,
                             text_color=TEXT, dropdown_font=font(13), font=font(13), dynamic_resizing=False)


def entry(master, var, **kw):
    return ctk.CTkEntry(master, textvariable=var, height=32, corner_radius=8, border_width=1,
                        border_color=BORDER, fg_color=FIELD, text_color=TEXT, font=font(13), **kw)


def switch(master, text, var, command=None):
    return ctk.CTkSwitch(master, text=text, variable=var, onvalue=True, offvalue=False, command=command,
                         progress_color=ACCENT, font=font(13), text_color=TEXT, switch_width=34, switch_height=18)


def labeled_row(master, label, widget_factory):
    row = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(row, text=label, font=font(13), text_color=MUTED, anchor="w").pack(side="left")
    w = widget_factory(row)
    w.pack(side="right")
    return row


def theme_icon(kind: str, px: int = 18):
    """Gambar ikon matahari / bulan (tanpa file aset) supaya tajam di Mac & Windows."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    s = px * 4

    def draw(color):
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if kind == "sun":
            import math
            c, r = s / 2, s * 0.2
            d.ellipse((c - r, c - r, c + r, c + r), fill=color)
            for i in range(8):
                a = i * math.pi / 4
                x1, y1 = c + math.cos(a) * s * 0.31, c + math.sin(a) * s * 0.31
                x2, y2 = c + math.cos(a) * s * 0.44, c + math.sin(a) * s * 0.44
                d.line((x1, y1, x2, y2), fill=color, width=max(2, s // 14))
        else:
            d.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=color)
            d.ellipse((s * 0.34, s * 0.02, s * 1.04, s * 0.72), fill=(0, 0, 0, 0))
        return im.resize((px * 2, px * 2), Image.LANCZOS)

    return ctk.CTkImage(light_image=draw(TEXT[0]), dark_image=draw(TEXT[1]), size=(px, px))


# ───────────────────────── aplikasi ─────────────────────────

class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.placeholder_on = False
        self.dry = False
        cfg = load_cfg()

        if os.environ.get("BD_THEME") in ("light", "dark"):   # dipakai CI untuk screenshot kedua tema
            cfg["theme"] = os.environ["BD_THEME"]
        theme = cfg.get("theme")
        if theme not in ("light", "dark"):   # pertama kali dibuka: ikuti tema sistem, setelah itu pakai pilihanmu
            theme = "dark" if ctk.get_appearance_mode().lower() == "dark" else "light"
        self.theme = theme
        ctk.set_appearance_mode(theme)
        root.title(APP)
        root.configure(fg_color=BG)
        self._set_icons()

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1120, sw - 40), min(760, sh - (110 if sh < 900 else 90))
        root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{0 if sh < 900 else 40}")
        root.minsize(900, min(560, sh - 90))

        # ── state ──
        self.v_out = tk.StringVar(value=cfg.get("out", DEFAULT_OUT))
        self.v_workers = tk.StringVar(value=str(cfg.get("workers", 3)))
        self.v_retries = tk.StringVar(value=str(cfg.get("retries", 6)))
        self.v_keepzip = tk.BooleanVar(value=cfg.get("keep_zip", False))
        self.v_autoopen = tk.BooleanVar(value=cfg.get("auto_open", True))
        self.v_quality = tk.StringVar(value=D.QUALITY_LABELS.get(cfg.get("video_quality", "best"), "Terbaik"))
        self.browsers = {"Tidak pakai": None, "Chrome": "chrome", "Safari": "safari", "Firefox": "firefox",
                         "Edge": "edge", "Brave": "brave", "Opera": "opera", "Vivaldi": "vivaldi"}
        self.v_browser = tk.StringVar(value=next((k for k, v in self.browsers.items()
                                                   if v == cfg.get("cookies_browser")), "Tidak pakai"))
        self.last_out: Path | None = None
        # API key disimpan di Keychain / Credential Manager (bukan file teks). Pindahkan key lama kalau ada.
        old = cfg.pop("api_key", "")
        if old and D.keychain_set(old):
            save_cfg(cfg)
        self.v_apikey = tk.StringVar(value=D.keychain_get() or old or "")
        self._key_job = None
        self.v_cookies = tk.StringVar(value=cfg.get("cookies", ""))
        self.v_showkey = tk.BooleanVar(value=False)

        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        self._build_header()
        body = ctk.CTkFrame(root, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 18))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=340)
        body.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=5)
        left.grid_rowconfigure(1, weight=4)
        self._build_links(left)
        self._build_activity(left)
        self._build_sidebar(body)

        draft = cfg.get("links", "")
        if draft.strip():
            self.links.insert("1.0", draft)
        else:
            self._ph_show()
        self._update_count()
        self.v_apikey.trace_add("write", lambda *_: self._schedule_key_save())
        self._key_status()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(150, self.poll)

    def _set_icons(self):
        self.logo = None
        png = BASE / "assets" / "icon_256.png"
        try:
            from PIL import Image
            img = Image.open(png)
            self.logo = ctk.CTkImage(light_image=img, dark_image=img, size=(40, 40))
        except Exception:  # noqa: BLE001
            pass
        try:
            self._icon_photo = tk.PhotoImage(file=str(png))
            self.root.iconphoto(True, self._icon_photo)
        except tk.TclError:
            pass
        ico = BASE / "assets" / "icon.ico"
        if IS_WIN and ico.exists():  # CustomTkinter memasang ikon bawaannya setelah ±200 ms → timpa lagi
            self.root.after(300, lambda: self.root.iconbitmap(str(ico)))

    # ── header ──
    def _build_header(self):
        head = ctk.CTkFrame(self.root, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 14))
        if self.logo:
            ctk.CTkLabel(head, text="", image=self.logo).pack(side="left", padx=(0, 12))
        t = ctk.CTkFrame(head, fg_color="transparent")
        t.pack(side="left")
        ctk.CTkLabel(t, text=APP, font=font(22, "bold"), text_color=TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(t, text="Drive · Dropbox · OneDrive · MediaFire · MEGA · YouTube, Instagram, TikTok & ±1800 situs",
                     font=font(12), text_color=MUTED, anchor="w").pack(anchor="w")
        self.theme_btn = ctk.CTkButton(head, text="", width=36, height=36, corner_radius=18,
                                       fg_color=SUBTLE_BTN, hover_color=SUBTLE_HOVER, command=self._toggle_theme)
        self.theme_btn.pack(side="right")
        self._theme_icon()

    def _toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        ctk.set_appearance_mode(self.theme)
        self._theme_icon()
        self.persist()

    def _theme_icon(self):
        """Mode terang → ikon bulan (klik untuk gelap); mode gelap → ikon matahari (klik untuk terang)."""
        img = theme_icon("sun" if self.theme == "dark" else "moon")
        if img:
            self._theme_img = img
            self.theme_btn.configure(image=img, text="")
        else:
            self.theme_btn.configure(text="☀" if self.theme == "dark" else "☾", font=font(16), text_color=TEXT)

    # ── kiri atas: link ──
    def _build_links(self, parent):
        c = card(parent)
        c.grid(row=0, column=0, sticky="nsew", pady=(0, 14))
        c.grid_columnconfigure(0, weight=1)
        c.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(c, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        section_title(top, "Link download").pack(side="left")
        self.count_lbl = ctk.CTkLabel(top, text="", font=font(12, "bold"), text_color=ACCENT,
                                      fg_color=SUBTLE_BTN, corner_radius=10, height=22)
        self.count_lbl.pack(side="right")
        self.links = ctk.CTkTextbox(c, font=font(13, mono=True), wrap="none", corner_radius=10,
                                    fg_color=FIELD, border_width=1, border_color=BORDER, text_color=TEXT,
                                    undo=True)
        self.links.grid(row=1, column=0, sticky="nsew", padx=16)
        self.links_tb = self.links._textbox
        self.links_tb.bind("<FocusIn>", lambda e: self._ph_clear())
        self.links_tb.bind("<FocusOut>", lambda e: self._ph_show())
        self.links_tb.bind("<<Modified>>", self._on_links_changed)
        bar = ctk.CTkFrame(c, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 14))
        subtle_button(bar, "Tempel", self.paste, width=90).pack(side="left")
        subtle_button(bar, "Impor .txt…", self.import_txt, width=110).pack(side="left", padx=8)
        subtle_button(bar, "Bersihkan", self.clear_links, width=100).pack(side="left")

    # ── kiri bawah: progres & log ──
    def _build_activity(self, parent):
        c = card(parent)
        c.grid(row=1, column=0, sticky="nsew")
        c.grid_columnconfigure(0, weight=1)
        c.grid_rowconfigure(3, weight=1)
        top = ctk.CTkFrame(c, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.status = ctk.CTkLabel(top, text="Siap", font=font(15, "bold"), text_color=TEXT, anchor="w")
        self.status.pack(side="left")
        self.stats = ctk.CTkLabel(top, text="", font=font(12), text_color=MUTED, anchor="e")
        self.stats.pack(side="right")
        self.pbar = ctk.CTkProgressBar(c, height=8, corner_radius=4, progress_color=ACCENT, fg_color=SUBTLE_BTN)
        self.pbar.grid(row=1, column=0, sticky="ew", padx=16)
        self.pbar.set(0)
        self.active = ctk.CTkLabel(c, text="", font=font(12), text_color=MUTED, anchor="w")
        self.active.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 4))
        self.log = ctk.CTkTextbox(c, font=font(12, mono=True), wrap="none", corner_radius=10, fg_color=FIELD,
                                  border_width=1, border_color=BORDER, text_color=TEXT, height=120)
        self.log.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 14))
        for tag, col in (("ok", OK), ("fail", FAIL), ("wait", WAIT), ("dim", DIM)):
            self.log.tag_config(tag, foreground=col)
        self.log_tb = self.log._textbox
        self.log_tb.bind("<Double-Button-1>", self._reveal_log_line)
        self._log_hint()

    def _log_hint(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", f"Log aktivitas muncul di sini.\nKlik dua kali baris ✓ untuk membukanya di {FM}.\n", "dim")
        self.log.configure(state="disabled")

    # ── kanan: pengaturan + tombol aksi ──
    def _build_sidebar(self, parent):
        side = ctk.CTkFrame(parent, fg_color="transparent", width=340)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_rowconfigure(0, weight=1)
        side.grid_columnconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(side, fg_color="transparent", width=320,
                                        scrollbar_button_color=SUBTLE_BTN, scrollbar_button_hover_color=SUBTLE_HOVER)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # simpan ke
        c = card(scroll)
        c.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        section_title(c, "Simpan ke").pack(anchor="w", padx=16, pady=(14, 8))
        entry(c, self.v_out).pack(fill="x", padx=16)
        r = ctk.CTkFrame(c, fg_color="transparent")
        r.pack(fill="x", padx=16, pady=(8, 0))
        subtle_button(r, "Pilih folder…", self.pick_out).pack(side="left", expand=True, fill="x")
        subtle_button(r, f"Buka di {FM}", lambda: open_in_finder(self.v_out.get())).pack(
            side="left", expand=True, fill="x", padx=(8, 0))
        switch(c, f"Buka {FM} setelah selesai", self.v_autoopen).pack(anchor="w", padx=16, pady=(10, 14))

        # video
        c = card(scroll)
        c.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        section_title(c, "Video", "YouTube, IG, TikTok…").pack(anchor="w", padx=16, pady=(14, 8))
        labeled_row(c, "Kualitas", lambda m: option(m, self.v_quality, list(D.QUALITY_LABELS.values()))
                    ).pack(fill="x", padx=16)
        labeled_row(c, "Login dari browser", lambda m: option(m, self.v_browser, list(self.browsers))
                    ).pack(fill="x", padx=16, pady=(8, 0))
        self.vid_info = ctk.CTkLabel(c, text="", font=font(11), text_color=MUTED, justify="left",
                                     anchor="w", wraplength=280)
        self.vid_info.pack(fill="x", padx=16, pady=(8, 0))
        self.b_upd = subtle_button(c, "Update mesin video", self.update_ytdlp)
        if not FROZEN:  # di .exe yt-dlp sudah dibundel; update = unduh versi aplikasi terbaru
            self.b_upd.pack(fill="x", padx=16, pady=(8, 0))
        ctk.CTkFrame(c, fg_color="transparent", height=14).pack()
        self._video_status()

        # unduhan
        c = card(scroll)
        c.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        section_title(c, "Unduhan").pack(anchor="w", padx=16, pady=(14, 8))
        labeled_row(c, "Download paralel", lambda m: option(m, self.v_workers, [str(i) for i in range(1, 9)], 80)
                    ).pack(fill="x", padx=16)
        labeled_row(c, "Coba ulang per file",
                    lambda m: option(m, self.v_retries, ["0", "1", "2", "3", "6", "10", "20"], 80)
                    ).pack(fill="x", padx=16, pady=(8, 0))
        switch(c, "Simpan ZIP Dropbox setelah diekstrak", self.v_keepzip).pack(anchor="w", padx=16, pady=(10, 14))

        # google drive (bisa dilipat)
        c = card(scroll)
        c.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 14))
        section_title(head, "Google Drive", "opsional").pack(side="left")
        self.gd_btn = ctk.CTkButton(head, text="", width=70, height=26, corner_radius=8, fg_color="transparent",
                                    hover_color=SUBTLE_HOVER, text_color=ACCENT, font=font(12, "bold"),
                                    command=self._toggle_gd)
        self.gd_btn.pack(side="right")
        self.gd_body = ctk.CTkFrame(c, fg_color="transparent")
        ctk.CTkLabel(self.gd_body, text="API key", font=font(13), text_color=MUTED, anchor="w").pack(fill="x")
        kr = ctk.CTkFrame(self.gd_body, fg_color="transparent")
        kr.pack(fill="x", pady=(4, 0))
        self.key_entry = entry(kr, self.v_apikey, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkCheckBox(kr, text="Lihat", variable=self.v_showkey, onvalue=True, offvalue=False, width=60,
                        checkbox_width=18, checkbox_height=18, font=font(12), text_color=MUTED,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        command=lambda: self.key_entry.configure(show="" if self.v_showkey.get() else "•")
                        ).pack(side="left", padx=(8, 0))
        self.key_msg = ctk.CTkLabel(self.gd_body, text="", font=font(11), text_color=OK, anchor="w")
        self.key_msg.pack(fill="x")
        ctk.CTkLabel(self.gd_body, text="cookies.txt", font=font(13), text_color=MUTED, anchor="w").pack(fill="x", pady=(4, 0))
        cr = ctk.CTkFrame(self.gd_body, fg_color="transparent")
        cr.pack(fill="x", pady=(4, 0))
        entry(cr, self.v_cookies).pack(side="left", fill="x", expand=True)
        subtle_button(cr, "Pilih…", self.pick_cookies, width=64).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(self.gd_body, text="Membantu saat kena kuota Google Drive. Kosongkan kalau tidak punya.",
                     font=font(11), text_color=MUTED, wraplength=280, justify="left", anchor="w"
                     ).pack(fill="x", pady=(8, 14))
        self.gd_open = bool(self.v_apikey.get() or self.v_cookies.get()) and self.root.winfo_screenheight() >= 900
        self._render_gd()

        # tombol aksi (tetap terlihat, tidak ikut digulir)
        act = ctk.CTkFrame(side, fg_color="transparent")
        act.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.b_start = ctk.CTkButton(act, text="Mulai Download", command=lambda: self.start(False), height=44,
                                     corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     font=font(15, "bold"), text_color="#FFFFFF")
        self.b_start.pack(fill="x")
        r = ctk.CTkFrame(act, fg_color="transparent")
        r.pack(fill="x", pady=(8, 0))
        self.b_check = subtle_button(r, "Cek isi link", lambda: self.start(True))
        self.b_check.pack(side="left", expand=True, fill="x")
        self.b_stop = ctk.CTkButton(r, text="Stop", command=self.stop, height=30, corner_radius=8,
                                    fg_color=SUBTLE_BTN, hover_color=SUBTLE_HOVER, text_color=DANGER,
                                    text_color_disabled=MUTED, font=font(13), state="disabled")
        self.b_stop.pack(side="left", expand=True, fill="x", padx=(8, 0))

    def _toggle_gd(self):
        self.gd_open = not self.gd_open
        self._render_gd()

    def _render_gd(self):
        if self.gd_open:
            self.gd_body.pack(fill="x", padx=16)
        else:
            self.gd_body.pack_forget()
        self.gd_btn.configure(text="Tutup" if self.gd_open else "Atur")

    # ── placeholder kotak link ──
    def _ph_show(self):
        if not self.links.get("1.0", "end").strip():
            self.placeholder_on = True
            self.links.insert("1.0", PLACEHOLDER)
            self.links.configure(text_color=MUTED)
            self.links_tb.edit_modified(False)

    def _ph_clear(self):
        if self.placeholder_on:
            self.placeholder_on = False
            self.links.delete("1.0", "end")
            self.links.configure(text_color=TEXT)

    def links_text(self) -> str:
        return "" if self.placeholder_on else self.links.get("1.0", "end")

    def _on_links_changed(self, _e=None):
        if self.links_tb.edit_modified():
            self._update_count()
            self.links_tb.edit_modified(False)

    def _update_count(self):
        n = len(D.parse_lines(self.links_text().splitlines()))
        self.count_lbl.configure(text=f"  {n} link  ")

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
                  "theme": self.theme,
                  "links": self.links_text()})

    # ── API key: simpan otomatis setiap kali diubah ──
    def _key_status(self, text=None, color=OK):
        if text is None:
            text = "✓ Tersimpan aman di perangkat ini" if self.v_apikey.get().strip() else ""
        self.key_msg.configure(text=text, text_color=color)

    def _schedule_key_save(self):
        if self._key_job:
            self.root.after_cancel(self._key_job)
        self._key_status("menyimpan…", MUTED)
        self._key_job = self.root.after(700, self._save_key)

    def _save_key(self):
        self._key_job = None
        key = self.v_apikey.get().strip()
        if D.keychain_set(key):
            self._key_status("✓ Tersimpan aman di perangkat ini" if key else "Key dihapus", OK if key else MUTED)
        else:
            self._key_status("⚠ Gagal menyimpan", FAIL)

    # ── video ──
    def _quality_key(self) -> str:
        return next((k for k, v in D.QUALITY_LABELS.items() if v == self.v_quality.get()), "best")

    def _video_status(self):
        if not D.ytdlp_available():
            self.vid_info.configure(text="⚠ Mesin video (yt-dlp) belum terpasang. Klik “Update mesin video”.")
            return
        import yt_dlp
        hint = "winget install ffmpeg" if IS_WIN else "brew install ffmpeg"
        ff = "ffmpeg siap" if D.FFMPEG else f"ffmpeg belum ada ({hint}) untuk kualitas tertinggi & MP3"
        self.vid_info.configure(text=f"yt-dlp {yt_dlp.version.__version__} · {ff}. Playlist YouTube: 1 video "
                                     f"saja. IG/FB/video privat: pilih browser yang sudah login.")

    def update_ytdlp(self):
        self.b_upd.configure(state="disabled", text="Memperbarui…")

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
        self.b_upd.configure(state="normal", text="Update mesin video")
        if ok:
            import importlib
            if "yt_dlp" in sys.modules:
                messagebox.showinfo(APP, "yt-dlp sudah diperbarui. Tutup & buka lagi aplikasi supaya versi baru dipakai.")
            importlib.invalidate_caches()
            self._video_status()
        else:
            self.vid_info.configure(text=f"⚠ Gagal update: {err}")

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
        self.status.configure(text="Membaca isi link…")
        self.stats.configure(text="")
        self.active.configure(text="")
        self.pbar.configure(mode="indeterminate")
        self.pbar.start()
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
        self.status.configure(text="Menghentikan…")
        self.active.configure(text="Menunggu unduhan yang sedang berjalan berhenti")
        self.b_stop.configure(state="disabled")

    def _busy(self, on: bool):
        self.b_start.configure(state="disabled" if on else "normal",
                               text="Sedang mengunduh…" if on and not self.dry else "Mulai Download")
        self.b_check.configure(state="disabled" if on else "normal")
        self.b_stop.configure(state="normal" if on else "disabled")

    def _determinate(self):
        if self.pbar.cget("mode") != "determinate":
            self.pbar.stop()
            self.pbar.configure(mode="determinate")

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
        if self.worker and self.worker.is_alive() and not self.dry:
            s = D.P.snapshot()
            if s["total"]:
                self._determinate()
                fin = s["done"] + s["skipped"] + s["failed"]
                self.pbar.set(fin / s["total"])
                self.status.configure(text=f"Mengunduh {fin} dari {s['total']} file")
                fail = f"  ·  {s['failed']} gagal" if s["failed"] else ""
                self.stats.configure(text=f"{D.human(s['bytes'])}  ·  {D.human(s['speed'])}/s{fail}")
                act = s["active"]
                self.active.configure(text=("↓ " + ", ".join(Path(a).name or a for a in act[:3])
                                            + (f" +{len(act) - 3}" if len(act) > 3 else "")) if act else "")
        self.root.after(250, self.poll)

    def finish(self, res):
        self._determinate()
        self._busy(False)
        self.active.configure(text="")
        if not res:
            self.status.configure(text="Berhenti karena error")
            self.stats.configure(text="lihat log")
            return
        if res.get("dry_run"):
            self.pbar.set(0)
            self.status.configure(text=f"Ditemukan {res['jobs']} file")
            self.stats.configure(text="klik Mulai Download untuk mengunduh")
            return
        total = max(res["jobs"], 1)
        self.pbar.set((res["done"] + res["skipped"] + res["failed"]) / total)
        head = "Dihentikan" if res.get("cancelled") else ("Selesai dengan kegagalan" if res["failed"] else "Selesai")
        self.status.configure(text=head)
        self.stats.configure(text=f"{res['done']} diunduh  ·  {res['skipped']} sudah ada  ·  "
                                  f"{res['failed']} gagal  ·  {D.human(res['bytes'])}")
        notify(APP, f"{head}: {res['done']} diunduh, {res['failed']} gagal")
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
        """Klik dua kali baris log → tampilkan file/folder itu di Finder/Explorer."""
        idx = self.log_tb.index(f"@{event.x},{event.y}")
        line = self.log_tb.get(f"{idx} linestart", f"{idx} lineend")
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


def make_root() -> ctk.CTk:
    ctk.set_default_color_theme("blue")
    return ctk.CTk()


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
        root = make_root()
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
    root = make_root()
    App(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after(400, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
