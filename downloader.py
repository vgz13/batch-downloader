#!/usr/bin/env python3
"""
Batch Downloader — Google Drive, Dropbox, OneDrive/SharePoint, MediaFire, MEGA, dan link langsung.

- Struktur folder dari sumber (Drive/Dropbox/MediaFire/MEGA) ikut dibuat ulang di lokasi tujuan.
- Resume otomatis: file yang sudah selesai dilewati, file setengah jalan dilanjutkan (.part).
- Anti-limit: throttle request Drive, retry + exponential backoff saat kena kuota/429,
  opsi cookies akun Google (kuota per-akun) dan Google API key (jalur kuota terpisah).

Contoh:
  python3 downloader.py "https://drive.google.com/drive/folders/XXXX" -o ~/Downloads/Aset
  python3 downloader.py -i links.txt --pick
  python3 downloader.py              # mode interaktif
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

try:
    import requests
except ImportError:
    sys.exit("Butuh library 'requests'. Install dulu:  python3 -m pip install requests")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
CHUNK = 1024 * 1024
TIMEOUT = (20, 120)


# ───────────────────────── util ─────────────────────────

class RetryableError(Exception):
    """Kuota/limit/error sementara — boleh dicoba lagi."""


class QuotaError(RetryableError):
    """Google Drive: 'Download quota exceeded' / 'Too many users'."""


class FatalError(Exception):
    """Tidak bisa diunduh (private, 404, dll)."""


class Cancelled(Exception):
    """Dihentikan oleh pengguna."""


@dataclass
class Job:
    url: str                      # URL unduhan atau ID (tergantung kind)
    dir: Path                     # folder relatif terhadap output
    name: str | None = None       # nama file (None = ambil dari header server)
    kind: str = "http"            # http | gdrive | gexport | mediafire | zip | mega
    size: int | None = None
    source: str = ""              # link yang bisa dipakai ulang di failed_links.txt
    meta: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.name:
            return str(self.dir / self.name)
        if self.meta.get("title"):
            return str(self.dir / f"🎬 {self.meta['title']}")
        return f"{self.dir}/ ← {self.source or self.url}" if str(self.dir) != "." else (self.source or self.url)


def sanitize(name: str) -> str:
    name = html.unescape(name or "")
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name).strip().rstrip(". ")
    return name[:240] or "untitled"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def filename_from_response(r: requests.Response) -> str | None:
    cd = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", cd)
    if m:
        return sanitize(unquote(m.group(1).strip('"')))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd) or re.search(r"filename\s*=\s*([^;]+)", cd)
    if m:
        raw = m.group(1).strip()
        try:  # header kadang berisi UTF-8 yang terbaca latin-1
            raw = raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return sanitize(raw)
    tail = unquote(urlparse(r.url).path.rsplit("/", 1)[-1])
    return sanitize(tail) if tail and "." in tail else None


_tls = threading.local()
_cookie_file: str | None = None


def session() -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        if _cookie_file:
            jar = MozillaCookieJar(_cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            s.cookies.update(jar)
        _tls.s = s
    return s


class Throttle:
    """Memberi jeda minimum antar request ke satu host (mis. Google Drive)."""

    def __init__(self, gap: float):
        self.gap, self.lock, self.last = gap, threading.Lock(), 0.0

    def wait(self):
        if self.gap <= 0:
            return
        with self.lock:
            delta = self.last + self.gap + random.uniform(0, self.gap * 0.5) - time.time()
            if delta > 0:
                time.sleep(delta)
            self.last = time.time()


# ───────────────────────── penyimpanan API key (macOS Keychain) ─────────────────────────

KEYCHAIN_SERVICE = "BatchDownloader"


def _keyring():
    try:
        import keyring  # Windows Credential Manager / Linux Secret Service
        return keyring
    except ImportError:
        return None


def keychain_get(account: str = "gdrive_api_key") -> str | None:
    """Ambil key tersimpan: Keychain (macOS) atau Credential Manager (Windows)."""
    if sys.platform != "darwin":
        kr = _keyring()
        try:
            return kr.get_password(KEYCHAIN_SERVICE, account) if kr else None
        except Exception:
            return None
    p = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True)
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def keychain_set(value: str | None, account: str = "gdrive_api_key") -> bool:
    """Simpan (atau hapus kalau kosong) key di Keychain/Credential Manager. True kalau berhasil."""
    if sys.platform != "darwin":
        kr = _keyring()
        if not kr:
            return False
        try:
            if value:
                kr.set_password(KEYCHAIN_SERVICE, account, value)
            else:
                try:
                    kr.delete_password(KEYCHAIN_SERVICE, account)
                except Exception:
                    pass
            return True
        except Exception:
            return False
    if not value:
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                       capture_output=True)
        return True
    p = subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account,
                        "-l", "Batch Downloader – Google Drive API key", "-w", value],
                       capture_output=True, text=True)
    return p.returncode == 0


# ───────────────────────── tampilan progres ─────────────────────────

class Progress:
    def __init__(self):
        self.lock = threading.Lock()
        self.total = self.done = self.skipped = self.failed = 0
        self.bytes = 0
        self.active: dict[int, str] = {}
        self.t0 = time.time()
        self.tty = sys.stdout.isatty()
        self._stop = threading.Event()
        self.cancel = threading.Event()   # di-set oleh tombol Stop (GUI) / Ctrl+C
        self.sink = None                  # callback log untuk GUI; None = print ke terminal

    def reset(self):
        with self.lock:
            self.total = self.done = self.skipped = self.failed = 0
            self.bytes = 0
            self.active.clear()
            self.t0 = time.time()
        self._stop = threading.Event()
        self.cancel.clear()

    def snapshot(self) -> dict:
        with self.lock:
            el = max(time.time() - self.t0, 0.1)
            return {"total": self.total, "done": self.done, "skipped": self.skipped, "failed": self.failed,
                    "bytes": self.bytes, "speed": self.bytes / el, "elapsed": el,
                    "active": list(self.active.values())}

    def log(self, msg: str):
        if self.sink:
            self.sink(msg)
            return
        with self.lock:
            if self.tty:
                sys.stdout.write("\r\033[K")
            print(msg, flush=True)

    def add_bytes(self, n: int):
        with self.lock:
            self.bytes += n

    def _status(self) -> str:
        el = max(time.time() - self.t0, 0.1)
        fin = self.done + self.skipped + self.failed
        return (f"⬇ {len(self.active)} aktif | {fin}/{self.total} file | "
                f"{human(self.bytes)} | {human(self.bytes / el)}/s")

    def start_ticker(self):
        if not self.tty or self.sink:
            return

        def run():
            while not self._stop.wait(1.0):
                with self.lock:
                    sys.stdout.write("\r\033[K" + self._status())
                    sys.stdout.flush()
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()
        if self.tty:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


P = Progress()


# ───────────────────────── resolver: Google Drive ─────────────────────────

GD_ID = r"([\w-]{20,})"
GDOC_EXPORT = {
    "document": ("https://docs.google.com/document/d/{id}/export?format=docx", ".docx"),
    "spreadsheets": ("https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx", ".xlsx"),
    "presentation": ("https://docs.google.com/presentation/d/{id}/export/pptx", ".pptx"),
    "drawings": ("https://docs.google.com/drawings/d/{id}/export/png", ".png"),
}
GAPI_EXPORT = {
    "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}


def gd_parse(url: str) -> tuple[str, str] | None:
    """-> (jenis, id). jenis: folder | file | document | spreadsheets | presentation | drawings"""
    u = urlparse(url)
    if not any(h in u.netloc for h in ("drive.google.com", "docs.google.com", "drive.usercontent.google.com")):
        return None
    m = re.search(r"/folders/" + GD_ID, u.path)
    if m:
        return "folder", m.group(1)
    m = re.search(r"/(document|spreadsheets|presentation|drawings)/d/" + GD_ID, u.path)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"/file/d/" + GD_ID, u.path)
    if m:
        return "file", m.group(1)
    qid = parse_qs(u.query).get("id", [None])[0]
    if qid:
        return ("folder" if "folderview" in u.path else "file"), qid
    return None


class GDrive:
    def __init__(self, api_key: str | None, throttle: Throttle):
        self.key = api_key
        self.th = throttle
        self.dirs: set[Path] = set()   # semua folder (termasuk yang kosong) untuk dibuat ulang

    # ---- listing via halaman embed publik (tanpa API key) ----
    def _list_web(self, fid: str) -> tuple[str, list[tuple[str, str, str]]]:
        self.th.wait()
        r = session().get("https://drive.google.com/embeddedfolderview", params={"id": fid}, timeout=TIMEOUT)
        if r.status_code == 404 or "flip-entry" not in r.text and "<title>" not in r.text:
            raise FatalError(f"Folder Drive {fid} tidak bisa dibuka (private / tidak dibagikan?)")
        if r.status_code in (429, 500, 502, 503):
            raise RetryableError(f"HTTP {r.status_code} saat listing folder")
        title = re.search(r"<title>(.*?)</title>", r.text, re.S)
        title = sanitize(title.group(1)) if title else fid
        items = []
        for eid, href, name in re.findall(
                r'<div class="flip-entry" id="entry-([\w-]+)".*?<a href="([^"]+)".*?'
                r'<div class="flip-entry-title">(.*?)</div>', r.text, re.S):
            href = html.unescape(href)
            if "/folders/" in href:
                kind = "folder"
            else:
                m = re.search(r"/(document|spreadsheets|presentation|drawings)/d/", href)
                kind = m.group(1) if m else "file"
            items.append((kind, eid, sanitize(name)))
        return title, items

    # ---- listing via Drive API v3 (pakai API key, tanpa batas jumlah) ----
    def _api(self, path: str, **params):
        params.update(key=self.key, supportsAllDrives="true")
        self.th.wait()
        r = session().get(f"https://www.googleapis.com/drive/v3/{path}", params=params, timeout=TIMEOUT)
        if r.status_code in (403, 429, 500, 503) and "rate" in r.text.lower():
            raise RetryableError(f"Drive API {r.status_code}")
        if r.status_code != 200:
            raise FatalError(f"Drive API {r.status_code}: {r.text[:200]}")
        return r.json()

    def _list_api(self, fid: str):
        meta = self._api(f"files/{fid}", fields="name")
        items, token = [], None
        while True:
            data = self._api("files", q=f"'{fid}' in parents and trashed=false", pageSize=1000,
                             includeItemsFromAllDrives="true", pageToken=token or "",
                             fields="nextPageToken,files(id,name,mimeType,size)")
            for f in data.get("files", []):
                items.append(f)
            token = data.get("nextPageToken")
            if not token:
                break
        return sanitize(meta["name"]), items

    def _with_retry(self, fn, *a):
        for i in range(5):
            try:
                return fn(*a)
            except RetryableError as e:
                w = 10 * 2 ** i
                P.log(f"  ⏳ listing kena limit ({e}), tunggu {w}s…")
                time.sleep(w)
        return fn(*a)

    def resolve(self, url: str, base: Path) -> list[Job]:
        kind, gid = gd_parse(url)
        if kind == "folder":
            return self._walk(gid, base, root=True)
        if kind in GDOC_EXPORT:
            tmpl, ext = GDOC_EXPORT[kind]
            return [Job(tmpl.format(id=gid), base, None, "gexport", source=url, meta={"ext": ext, "id": gid})]
        return [Job(gid, base, None, "gdrive", source=f"https://drive.google.com/file/d/{gid}/view")]

    def _walk(self, fid: str, base: Path, root=False) -> list[Job]:
        jobs: list[Job] = []
        if self.key:
            title, items = self._with_retry(self._list_api, fid)
            here = base / title if root else base
            self.dirs.add(here)
            P.log(f"  📁 {here}  ({len(items)} item)")
            for f in items:
                name, mt = sanitize(f["name"]), f["mimeType"]
                if mt == "application/vnd.google-apps.folder":
                    jobs += self._walk(f["id"], here / name)
                elif mt in GAPI_EXPORT:
                    emt, ext = GAPI_EXPORT[mt]
                    if not name.lower().endswith(ext):
                        name += ext
                    jobs.append(Job(f["id"], here, name, "gexport",
                                    source=f"https://drive.google.com/file/d/{f['id']}/view",
                                    meta={"api_mime": emt, "id": f["id"], "ext": ext}))
                elif mt.startswith("application/vnd.google-apps."):
                    P.log(f"  ⚠ dilewati (tipe Google {mt.split('.')[-1]} tidak bisa diunduh): {name}")
                else:
                    jobs.append(Job(f["id"], here, name, "gdrive", size=int(f.get("size") or 0) or None,
                                    source=f"https://drive.google.com/file/d/{f['id']}/view"))
            return jobs

        title, items = self._with_retry(self._list_web, fid)
        here = base / title if root else base
        self.dirs.add(here)
        P.log(f"  📁 {here}  ({len(items)} item)")
        for kind, eid, name in items:
            if kind == "folder":
                jobs += self._walk(eid, here / name)
            elif kind in GDOC_EXPORT:
                tmpl, ext = GDOC_EXPORT[kind]
                if not name.lower().endswith(ext):
                    name += ext
                jobs.append(Job(tmpl.format(id=eid), here, name, "gexport",
                                source=f"https://docs.google.com/{kind}/d/{eid}", meta={"ext": ext, "id": eid}))
            else:
                jobs.append(Job(eid, here, name, "gdrive", source=f"https://drive.google.com/file/d/{eid}/view"))
        return jobs


# ───────────────────────── resolver: Dropbox ─────────────────────────

def dropbox_resolve(url: str, base: Path) -> list[Job]:
    u = urlparse(url)
    q = parse_qs(u.query)
    q.pop("raw", None)
    q["dl"] = ["1"]
    direct = urlunparse(u._replace(query=urlencode(q, doseq=True)))
    is_folder = "/scl/fo/" in u.path or u.path.startswith("/sh/")
    if is_folder:  # Dropbox mengirim folder sebagai ZIP → diekstrak dengan struktur aslinya
        return [Job(direct, base, None, "zip", source=url, meta={"expect_file": True})]
    return [Job(direct, base, None, "http", source=url, meta={"expect_file": True})]


# ───────────────────────── resolver: OneDrive / SharePoint ─────────────────────────

def onedrive_resolve(url: str, base: Path) -> list[Job]:
    enc = "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    try:
        r = session().get(f"https://api.onedrive.com/v1.0/shares/{enc}/root",
                          params={"expand": "children"}, timeout=TIMEOUT)
        if r.status_code == 200:
            return _od_walk(enc, r.json(), base, url, root=True)
    except (requests.RequestException, ValueError):
        pass
    # fallback: paksa link jadi unduhan langsung
    u = urlparse(url)
    q = parse_qs(u.query)
    q["download"] = ["1"]
    return [Job(urlunparse(u._replace(query=urlencode(q, doseq=True))), base, None, "http", source=url,
                meta={"expect_file": True})]


def _od_walk(enc, item, base, src, root=False):
    name = sanitize(item.get("name", "onedrive"))
    if "folder" not in item:
        return [Job(item["@content.downloadUrl"], base, name, "http", size=item.get("size"), source=src)]
    here = base / name
    jobs = []
    children = item.get("children")
    if children is None:
        r = session().get(f"https://api.onedrive.com/v1.0/shares/{enc}/items/{item['id']}",
                          params={"expand": "children"}, timeout=TIMEOUT)
        children = r.json().get("children", []) if r.ok else []
    P.log(f"  📁 {here}  ({len(children)} item)")
    for c in children:
        jobs += _od_walk(enc, c, here, src)
    return jobs


# ───────────────────────── resolver: MediaFire ─────────────────────────

MF_API = "https://www.mediafire.com/api/1.5"


def mediafire_resolve(url: str, base: Path) -> list[Job]:
    m = re.search(r"/folder/(\w+)", url)
    if not m:
        return [Job(url, base, None, "mediafire", source=url, meta={"expect_file": True})]
    key = m.group(1)
    info = session().get(f"{MF_API}/folder/get_info.php",
                         params={"folder_key": key, "response_format": "json"}, timeout=TIMEOUT).json()
    name = sanitize(info.get("response", {}).get("folder_info", {}).get("name", key))
    return _mf_walk(key, base / name)


def _mf_content(key, ctype):
    out, chunk = [], 1
    while True:
        d = session().get(f"{MF_API}/folder/get_content.php", params={
            "folder_key": key, "content_type": ctype, "chunk": chunk, "chunk_size": 1000,
            "response_format": "json"}, timeout=TIMEOUT).json()
        fc = d.get("response", {}).get("folder_content", {})
        out += fc.get(ctype, []) or []
        if fc.get("more_chunks") != "yes":
            return out
        chunk += 1


def _mf_walk(key, here: Path) -> list[Job]:
    files, folders = _mf_content(key, "files"), _mf_content(key, "folders")
    P.log(f"  📁 {here}  ({len(files) + len(folders)} item)")
    jobs = [Job(f["links"]["normal_download"], here, sanitize(f["filename"]), "mediafire",
                size=int(f.get("size") or 0) or None, source=f["links"]["normal_download"]) for f in files]
    for d in folders:
        jobs += _mf_walk(d["folderkey"], here / sanitize(d["name"]))
    return jobs


def mediafire_direct(page_url: str) -> str:
    r = session().get(page_url, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RetryableError(f"MediaFire HTTP {r.status_code}")
    m = re.search(r'data-scrambled-url="([^"]+)"', r.text)
    if m:
        return base64.b64decode(m.group(1)).decode()
    m = re.search(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', r.text)
    if m:
        return html.unescape(m.group(1))
    raise FatalError("Link unduh MediaFire tidak ditemukan (file dihapus/private?)")


# ───────────────────────── resolver: video (yt-dlp) ─────────────────────────
# YouTube, Instagram, Facebook, TikTok, X/Twitter, Vimeo, Reddit, Twitch, SoundCloud, dan ±1800 situs lain.

VIDEO = {"quality": "best", "browser": None, "cookies": None}   # diisi run_batch dari opsi
_extractors = None
FFMPEG = shutil.which("ffmpeg") or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
                                          if os.path.exists(p)), None)
QUALITY_LABELS = {"best": "Terbaik", "1080": "1080p", "720": "720p", "480": "480p", "audio": "Audio saja (MP3)"}


def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def is_video_url(url: str) -> bool:
    """True kalau yt-dlp punya extractor khusus untuk link ini (bukan 'generic')."""
    global _extractors
    if not ytdlp_available():
        return False
    if _extractors is None:
        from yt_dlp.extractor import gen_extractor_classes
        _extractors = [ie for ie in gen_extractor_classes() if ie.ie_key() not in ("Generic",)]
    return any(ie.suitable(url) for ie in _extractors)


class _QuietLog:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _ydl_base_opts() -> dict:
    o = {"logger": _QuietLog(), "quiet": True, "no_warnings": True, "noprogress": True, "retries": 10, "fragment_retries": 10,
         "extractor_retries": 5, "sleep_interval_requests": 0.5, "socket_timeout": 30}
    if VIDEO["browser"]:
        o["cookiesfrombrowser"] = (VIDEO["browser"],)
    elif VIDEO["cookies"]:
        o["cookiefile"] = VIDEO["cookies"]
    if FFMPEG:
        o["ffmpeg_location"] = FFMPEG
    return o


def _ydl_error(e: Exception) -> Exception:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "429" in low or "too many requests" in low or "rate-limit" in low or "rate limit" in low:
        return QuotaError(f"dibatasi situs (429): {msg[:160]}")
    if any(w in low for w in ("sign in", "login", "log in", "private", "cookies", "age", "members-only",
                              "confirm you", "not a bot")):
        return FatalError(f"{msg[:200]} → pilih 'Login dari browser' di pengaturan Video")
    if any(w in low for w in ("timed out", "connection", "temporarily", "http error 5", "incomplete")):
        return RetryableError(msg[:200])
    return FatalError(msg[:240])


def video_resolve(url: str, base: Path, depth: int = 0) -> list[Job]:
    """Baca info link video. Playlist/channel/album → satu subfolder berisi tiap video."""
    import yt_dlp
    opts = _ydl_base_opts() | {"extract_flat": "in_playlist", "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False, process=True)
    except yt_dlp.utils.DownloadError as e:
        raise _ydl_error(e)
    if info.get("_type") in ("playlist", "multi_video"):
        title = sanitize(info.get("title") or info.get("id") or "playlist")
        here = base / title
        entries = [e for e in (info.get("entries") or []) if e]
        P.log(f"  📁 {here}  ({len(entries)} item)")
        jobs = []
        for e in entries:
            u = e.get("url") or e.get("webpage_url")
            if not u:
                continue
            if not u.startswith("http"):
                u = e.get("webpage_url") or f"https://www.youtube.com/watch?v={u}"
            nested = e.get("_type") == "playlist" or any(k in (e.get("ie_key") or "") for k in ("Tab", "Playlist"))
            if nested and depth < 2:   # mis. channel → tab Videos / Shorts
                jobs += video_resolve(u, here, depth + 1)
            else:
                jobs.append(Job(u, here, None, "video", source=u, meta={"title": e.get("title") or u}))
        return jobs
    return [Job(url, base, None, "video", source=url, meta={"title": info.get("title") or url})]


# ───────────────────────── dispatcher ─────────────────────────

def resolve(url: str, base: Path, gd: GDrive) -> list[Job]:
    host = urlparse(url).netloc.lower()
    if gd_parse(url):
        return gd.resolve(url, base)
    if "dropbox.com" in host or "dropboxusercontent.com" in host:
        return dropbox_resolve(url, base)
    if "1drv.ms" in host or "onedrive.live.com" in host:
        return onedrive_resolve(url, base)
    if "sharepoint.com" in host:
        u = urlparse(url)
        q = parse_qs(u.query)
        q["download"] = ["1"]
        return [Job(urlunparse(u._replace(query=urlencode(q, doseq=True))), base, None, "http", source=url)]
    if "mediafire.com" in host:
        return mediafire_resolve(url, base)
    if "mega.nz" in host or "mega.co.nz" in host:
        return [Job(url, base, None, "mega", source=url)]
    if is_video_url(url):
        return video_resolve(url, base)
    return [Job(url, base, None, "http", source=url)]


# ───────────────────────── downloader ─────────────────────────

QUOTA_HINTS = ("quota exceeded", "too many users have viewed", "download quota",
               "kuota download", "terlalu banyak pengguna")


class Downloader:
    def __init__(self, out: Path, gd: GDrive, opts):
        self.out, self.gd, self.o = out, gd, opts

    # -- request builder per jenis job --
    def _request(self, job: Job, headers: dict, use_api=False) -> requests.Response:
        s = session()
        if job.kind == "gdrive":
            self.gd.th.wait()
            if use_api:
                url = f"https://www.googleapis.com/drive/v3/files/{job.url}"
                return s.get(url, params={"alt": "media", "key": self.gd.key, "supportsAllDrives": "true",
                                          "acknowledgeAbuse": "true"},
                             headers=headers, stream=True, timeout=TIMEOUT)
            url = "https://drive.usercontent.google.com/download"
            return s.get(url, params={"id": job.url, "export": "download", "confirm": "t"},
                         headers=headers, stream=True, timeout=TIMEOUT)
        if job.kind == "gexport":
            self.gd.th.wait()
            if use_api and "api_mime" in job.meta:
                return s.get(f"https://www.googleapis.com/drive/v3/files/{job.meta['id']}/export",
                             params={"mimeType": job.meta["api_mime"], "key": self.gd.key},
                             stream=True, timeout=TIMEOUT)
            if "api_mime" in job.meta:  # dari listing API → pakai link export web biasa
                kind = {".docx": "document", ".xlsx": "spreadsheets", ".pptx": "presentation",
                        ".png": "drawings"}[job.meta["ext"]]
                return s.get(GDOC_EXPORT[kind][0].format(id=job.meta["id"]), stream=True, timeout=TIMEOUT)
            return s.get(job.url, stream=True, timeout=TIMEOUT)  # export tidak mendukung Range
        if job.kind == "mediafire":
            return s.get(mediafire_direct(job.url), headers=headers, stream=True, timeout=TIMEOUT)
        return s.get(job.url, headers=headers, stream=True, timeout=TIMEOUT)

    def _check_html(self, job: Job, r: requests.Response):
        """Kalau server balas HTML padahal kita minta file → deteksi kuota / private / konfirmasi."""
        ctype = r.headers.get("content-type", "")
        if "text/html" not in ctype:
            return None
        if job.meta.get("expect_file"):  # Dropbox/OneDrive/MediaFire seharusnya kirim file, bukan halaman web
            raise FatalError("server membalas halaman web, bukan file (link salah, dihapus, atau private)")
        if job.kind in ("http", "zip") and "drive" not in r.url:
            return None
        body = r.text[:200_000]
        low = body.lower()
        if any(h in low for h in QUOTA_HINTS):
            raise QuotaError("kuota download Google Drive habis untuk file ini")
        if "accounts.google.com" in r.url or "servicelogin" in low:
            raise FatalError("file private / butuh login (pakai --cookies kalau kamu punya akses)")
        form = re.search(r'<form[^>]+id="download-form"[^>]+action="([^"]+)"', body)
        if form:  # halaman "can't scan for viruses" → ikuti form konfirmasi
            params = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', body))
            return html.unescape(form.group(1)), params
        if job.kind in ("gdrive", "gexport"):
            raise RetryableError("Drive membalas halaman HTML, bukan file")
        return None

    def fetch(self, job: Job, use_api=False) -> str:
        if job.kind == "mega":
            return self._mega(job)
        if job.kind == "video":
            return self._video(job)
        tdir = self.out / job.dir
        tdir.mkdir(parents=True, exist_ok=True)
        if job.name and (tdir / job.name).exists() and job.kind != "zip":
            return "skip"
        tag = job.name or "dl-" + hashlib.sha1(job.url.encode()).hexdigest()[:12]
        part = tdir / f".{tag}.part"
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have and job.kind != "gexport" else {}

        r = self._request(job, headers, use_api)
        try:
            if r.status_code == 416:  # .part sudah lengkap
                r.close()
                r = None
                if not job.name:
                    part.unlink(missing_ok=True)
                    raise RetryableError("file .part tidak cocok, diunduh ulang")
            else:
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RetryableError(f"HTTP {r.status_code}")
                if r.status_code == 403:
                    txt = r.text[:5000].lower() if "html" in r.headers.get("content-type", "") or "json" in r.headers.get("content-type", "") else ""
                    if any(h in txt for h in QUOTA_HINTS) or "ratelimit" in txt or "rate limit" in txt:
                        raise QuotaError("kuota/limit tercapai (403)")
                    if job.kind in ("gdrive", "gexport"):
                        raise QuotaError("Drive menolak (403) — biasanya kuota")
                    raise FatalError("HTTP 403 (akses ditolak)")
                if r.status_code == 404:
                    raise FatalError("HTTP 404 (file tidak ada / link salah)")
                if r.status_code >= 400:
                    raise RetryableError(f"HTTP {r.status_code}")
                confirm = self._check_html(job, r)
                if confirm:
                    r.close()
                    action, params = confirm
                    r = session().get(action, params=params, headers=headers, stream=True, timeout=TIMEOUT)
                    if "text/html" in r.headers.get("content-type", ""):
                        self._check_html(job, r)
                        raise RetryableError("konfirmasi Drive gagal")

                name = job.name or filename_from_response(r) or job.meta.get("fallback") or tag
                if job.kind == "gexport" and not name.lower().endswith(job.meta.get("ext", "")):
                    name += job.meta.get("ext", "")
                final = tdir / name
                if job.name is None and final.exists() and job.kind != "zip":
                    r.close()
                    part.unlink(missing_ok=True)
                    job.name = name
                    return "skip"
                job.name = name
                if r.status_code != 206:
                    have = 0
                total = None
                if r.headers.get("content-length"):
                    total = int(r.headers["content-length"]) + have
                with open(part, "ab" if have else "wb") as f:
                    got = have
                    for chunk in r.iter_content(CHUNK):
                        if P.cancel.is_set():
                            raise Cancelled()
                        if chunk:
                            f.write(chunk)
                            got += len(chunk)
                            P.add_bytes(len(chunk))
                if total and got < total:
                    raise RetryableError(f"koneksi putus di {human(got)}/{human(total)} (akan dilanjutkan)")
        finally:
            if r is not None:
                r.close()

        final = tdir / job.name
        if job.kind == "zip" or (job.name or "").lower().endswith(".zip") and job.meta.get("extract"):
            return self._extract(part, tdir, job)
        os.replace(part, final)
        return "ok"

    def _extract(self, part: Path, tdir: Path, job: Job) -> str:
        stem = sanitize(Path(job.name).stem) if job.name else "dropbox"
        if not zipfile.is_zipfile(part):
            os.replace(part, tdir / job.name)
            return "ok"
        dest = tdir / stem
        dest_resolved = dest.resolve()
        with zipfile.ZipFile(part) as z:
            names = [n for n in z.namelist() if n not in ("/", "")]
            tops = {n.split("/", 1)[0] for n in names}
            strip = len(tops) == 1 and all("/" in n for n in names) and next(iter(tops)) == stem
            for info in z.infolist():
                rel = info.filename.lstrip("/")
                if strip:
                    rel = rel.split("/", 1)[1] if "/" in rel else ""
                if not rel:
                    continue
                target = (dest / rel).resolve()
                if not str(target).startswith(str(dest_resolved)):  # proteksi zip-slip
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists() and target.stat().st_size == info.file_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
        if self.o.keep_zip:
            os.replace(part, tdir / (job.name or f"{stem}.zip"))
        else:
            part.unlink(missing_ok=True)
        job.name = stem + "/"
        return "ok"

    def _video(self, job: Job) -> str:
        import yt_dlp
        tdir = self.out / job.dir
        tdir.mkdir(parents=True, exist_ok=True)
        q = VIDEO["quality"]
        state = {"last": 0, "file": None, "skip": False}

        def hook(d):
            if P.cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if d["status"] == "downloading":
                got = d.get("downloaded_bytes") or 0
                if got >= state["last"]:
                    P.add_bytes(got - state["last"])
                state["last"] = got
            elif d["status"] == "finished":
                state["last"] = 0
                state["file"] = d.get("filename")

        def pp_hook(d):
            if d["status"] == "finished" and d.get("info_dict", {}).get("filepath"):
                state["file"] = d["info_dict"]["filepath"]

        class Log:
            def debug(self, m):
                if "has already been recorded in the archive" in m or "has already been downloaded" in m:
                    state["skip"] = True
            info = debug
            def warning(self, m):
                pass
            def error(self, m):
                pass

        if q == "audio":
            fmt = "ba/b"
        elif q in ("1080", "720", "480"):
            fmt = f"bv*[height<={q}]+ba/b[height<={q}]/b" if FFMPEG else f"b[height<={q}]/b"
        else:
            fmt = "bv*+ba/b" if FFMPEG else "b"
        opts = _ydl_base_opts() | {
            "format": fmt, "noplaylist": True, "continuedl": True,
            "paths": {"home": str(tdir), "temp": str(tdir)},
            "outtmpl": "%(title).150B [%(id)s].%(ext)s",
            # catatan video yang sudah diunduh (dipisah untuk mode audio) → dilewati saat dijalankan ulang
            "download_archive": str(self.out / (".audio-archive.txt" if q == "audio" else ".video-archive.txt")),
            "concurrent_fragment_downloads": 4, "logger": Log(),
            "progress_hooks": [hook], "postprocessor_hooks": [pp_hook],
        }
        if q == "audio" and FFMPEG:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        elif FFMPEG:
            opts["merge_output_format"] = "mp4"
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([job.url])
        except yt_dlp.utils.DownloadCancelled:
            raise Cancelled()
        except yt_dlp.utils.DownloadError as e:
            if P.cancel.is_set():
                raise Cancelled()
            raise _ydl_error(e)
        if state["skip"] and not state["file"]:
            return "skip"
        if state["file"]:
            job.name = Path(state["file"]).name
        return "ok"

    def _mega(self, job: Job) -> str:
        tool = shutil.which("megatools") or shutil.which("megadl")
        if not tool:
            raise FatalError("link MEGA butuh 'megatools' →  brew install megatools")
        tdir = self.out / job.dir
        tdir.mkdir(parents=True, exist_ok=True)
        cmd = [tool, "dl", "--path", str(tdir), job.url] if tool.endswith("megatools") else \
              [tool, "--path", str(tdir), job.url]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            err = (p.stderr or p.stdout).strip()[-300:]
            if "bandwidth" in err.lower() or "limit" in err.lower():
                raise QuotaError("limit bandwidth MEGA")
            raise FatalError(f"megatools gagal: {err}")
        job.name = "(MEGA)"
        return "ok"

    def run_job(self, idx: int, job: Job) -> tuple[str, Job, str]:
        tid = threading.get_ident()
        with P.lock:
            P.active[tid] = job.label
        use_api = False
        err = ""
        try:
            if P.cancel.is_set():
                return "cancel", job, ""
            for attempt in range(self.o.retries + 1):
                try:
                    res = self.fetch(job, use_api)
                    return res, job, ""
                except QuotaError as e:
                    err = str(e)
                    if self.gd.key and not use_api and job.kind in ("gdrive", "gexport"):
                        P.log(f"  ↪ {job.label}: {e} → coba jalur Drive API")
                        use_api = True
                        continue
                    wait = min(self.o.max_wait, self.o.quota_wait * 2 ** attempt)
                except RetryableError as e:
                    err = str(e)
                    wait = min(self.o.max_wait, 5 * 2 ** attempt)
                except requests.RequestException as e:
                    err = f"jaringan: {e.__class__.__name__}"
                    wait = min(self.o.max_wait, 5 * 2 ** attempt)
                except FatalError as e:
                    return "fail", job, str(e)
                except Cancelled:
                    return "cancel", job, ""
                if attempt >= self.o.retries:
                    break
                wait += random.uniform(0, wait * 0.25)
                P.log(f"  ⏳ {job.label}: {err} — coba lagi {attempt + 1}/{self.o.retries} dalam {wait:.0f}s")
                if P.cancel.wait(wait):
                    return "cancel", job, ""
            return "fail", job, err
        except OSError as e:
            return "fail", job, f"disk: {e}"
        finally:
            with P.lock:
                P.active.pop(tid, None)


# ───────────────────────── input & CLI ─────────────────────────

def parse_lines(lines) -> list[tuple[str, Path]]:
    """Format per baris:  URL   atau   URL | subfolder/tujuan   (# = komentar)"""
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url, _, sub = line.partition("|")
        url = url.strip()
        if not re.match(r"https?://", url):
            continue
        sub = Path(*[sanitize(p) for p in re.split(r"[\\/]", sub.strip()) if p.strip()]) if sub.strip() else Path()
        out.append((url, sub))
    return out


def pick_folder_mac(prompt="Pilih folder tujuan download") -> Path | None:
    if sys.platform != "darwin":
        return None
    p = subprocess.run(["osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")'],
                       capture_output=True, text=True)
    return Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def run_batch(entries: list[tuple[str, Path]], out: Path, o) -> dict:
    """Mesin utama — dipakai oleh CLI dan GUI. `o` berisi opsi (lihat argparse di main)."""
    global _cookie_file
    _cookie_file = o.cookies or None
    _tls.__dict__.clear()
    P.reset()
    out = Path(out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    VIDEO.update(quality=getattr(o, "video_quality", "best") or "best",
                 browser=getattr(o, "cookies_browser", None) or None, cookies=o.cookies or None)
    gd = GDrive(o.gdrive_api_key, Throttle(o.gdrive_delay))
    P.log(f"\n📂 Tujuan: {out}\n🔎 Membaca {len(entries)} link…")
    jobs: list[Job] = []
    failed_links: list[str] = []
    for url, sub in entries:
        if P.cancel.is_set():
            break
        P.log(f"• {url}")
        try:
            got = resolve(url, sub, gd)
            jobs += got
        except (FatalError, RetryableError, requests.RequestException, ValueError, KeyError) as e:
            P.log(f"  ✗ gagal dibaca: {e}")
            failed_links.append(f"{url} | {sub}" if str(sub) != "." else url)

    # buang duplikat (link yang sama dimasukkan dua kali)
    seen, uniq = set(), []
    for j in jobs:
        k = (j.kind, j.url, str(j.dir), j.name)
        if k not in seen:
            seen.add(k)
            uniq.append(j)
    jobs = uniq

    known = sum(j.size or 0 for j in jobs)
    P.log(f"\n🧾 {len(jobs)} file siap" + (f" (± {human(known)} terdeteksi)" if known else ""))
    if o.dry_run:
        for j in jobs:
            P.log(f"   {j.dir / (j.name or ('🎬 ' + j.meta['title'] if j.meta.get('title') else '<nama dari server>'))}" + (f"  [{human(j.size)}]" if j.size else ""))
        return {"jobs": len(jobs), "dry_run": True}

    for d in gd.dirs:  # buat juga folder kosong supaya struktur sama persis dengan Drive
        (out / d).mkdir(parents=True, exist_ok=True)
    P.total = len(jobs)
    P.start_ticker()
    dl = Downloader(out, gd, o)
    with ThreadPoolExecutor(max_workers=max(1, o.workers)) as ex:
        futs = [ex.submit(dl.run_job, i, j) for i, j in enumerate(jobs)]
        for fut in as_completed(futs):
            res, job, err = fut.result()
            if res == "cancel":
                rel = str(job.dir) if str(job.dir) != "." else ""
                failed_links.append(f"{job.source or job.url} | {rel}" if rel else (job.source or job.url))
                continue
            with P.lock:
                if res == "ok":
                    P.done += 1
                elif res == "skip":
                    P.skipped += 1
                else:
                    P.failed += 1
                n = P.done + P.skipped + P.failed
            if res == "ok":
                P.log(f"[{n}/{P.total}] ✓ {job.label}")
            elif res == "skip":
                P.log(f"[{n}/{P.total}] = sudah ada, dilewati: {job.label}")
            else:
                P.log(f"[{n}/{P.total}] ✗ {job.label} — {err}")
                rel = str(job.dir) if str(job.dir) != "." else ""
                failed_links.append(f"{job.source or job.url} | {rel}" if rel else (job.source or job.url))
    P.stop()

    el = time.time() - P.t0
    if P.cancel.is_set():
        P.log("\n⏹ Dihentikan. Klik Mulai lagi untuk melanjutkan — file yang selesai dilewati, .part diteruskan.")
    P.log(f"\n✅ Selesai {P.done} | dilewati {P.skipped} | gagal {P.failed} | {human(P.bytes)} dalam {el:.0f}s")
    fl = out / "failed_links.txt"
    if failed_links:
        fl.write_text("# Jalankan ulang nanti (kuota Drive biasanya reset dalam ≤24 jam):\n"
                      f"#   python3 downloader.py -i \"{fl}\" -o \"{out}\"\n" + "\n".join(failed_links) + "\n",
                      encoding="utf-8")
        P.log(f"⚠ Daftar yang gagal disimpan di: {fl}")
    elif fl.exists():
        fl.unlink()
    return {"jobs": len(jobs), "done": P.done, "skipped": P.skipped, "failed": P.failed,
            "bytes": P.bytes, "cancelled": P.cancel.is_set(), "failed_file": str(fl) if failed_links else None}



def main():
    ap = argparse.ArgumentParser(description="Batch downloader Google Drive / Dropbox / OneDrive / MediaFire / MEGA / link langsung.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("links", nargs="*", help="link (boleh banyak)")
    ap.add_argument("-i", "--input", help="file teks berisi daftar link (1 link per baris, opsional 'URL | subfolder')")
    ap.add_argument("-o", "--out", help="folder tujuan download")
    ap.add_argument("--pick", action="store_true", help="pilih folder tujuan lewat dialog Finder (macOS)")
    ap.add_argument("-w", "--workers", type=int, default=3, help="jumlah download paralel (default 3)")
    ap.add_argument("--retries", type=int, default=6, help="maks percobaan ulang per file (default 6)")
    ap.add_argument("--quota-wait", type=float, default=60, help="jeda awal (detik) saat kena kuota Drive, naik 2x tiap retry (default 60)")
    ap.add_argument("--max-wait", type=float, default=900, help="jeda maksimum antar retry dalam detik (default 900)")
    ap.add_argument("--gdrive-delay", type=float, default=1.0, help="jeda minimum antar request ke Google Drive (default 1.0s)")
    ap.add_argument("--gdrive-api-key", default=None,
                    help="Google API key (default: yang tersimpan dari aplikasi di Keychain, atau env GDRIVE_API_KEY)")
    ap.add_argument("--cookies", help="cookies.txt (format Netscape) dari browser yang login Google → pakai kuota akunmu")
    ap.add_argument("--keep-zip", action="store_true", help="simpan file ZIP Dropbox setelah diekstrak")
    ap.add_argument("-q", "--video-quality", choices=list(QUALITY_LABELS), default="best",
                    help="kualitas video YouTube/IG/FB/TikTok dll.: best, 1080, 720, 480, audio (default best)")
    ap.add_argument("--cookies-browser", choices=["chrome", "safari", "firefox", "edge", "brave", "opera", "vivaldi"],
                    help="pakai login dari browser ini untuk video yang butuh login (IG, FB, video privat)")
    ap.add_argument("--dry-run", action="store_true", help="hanya tampilkan daftar file + struktur folder, tidak mengunduh")
    o = ap.parse_args()

    o.gdrive_api_key = o.gdrive_api_key or os.environ.get("GDRIVE_API_KEY") or keychain_get()
    if o.gdrive_api_key:
        print("🔑 Memakai Google API key tersimpan.")
    global _cookie_file
    if o.cookies:
        if not Path(o.cookies).is_file():
            sys.exit(f"File cookies tidak ditemukan: {o.cookies}")
        _cookie_file = o.cookies

    entries = parse_lines(o.links)
    if o.input:
        entries += parse_lines(Path(o.input).expanduser().read_text(encoding="utf-8").splitlines())
    if not entries:
        print("Tempel link (boleh banyak, 1 per baris; format opsional 'URL | subfolder').")
        print("Tekan Enter di baris kosong untuk mulai:")
        buf = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                break
            buf.append(line)
        entries = parse_lines(buf)
    if not entries:
        sys.exit("Tidak ada link yang valid.")

    out = Path(o.out).expanduser() if o.out else None
    if out is None and o.pick:
        out = pick_folder_mac()
    if out is None:
        default = Path.home() / "Downloads" / "BatchDownloader"
        ans = input(f"Folder tujuan [Enter = {default}, ketik 'p' = pilih via Finder]: ").strip()
        if ans.lower() == "p":
            out = pick_folder_mac() or default
        else:
            out = Path(ans).expanduser() if ans else default
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    run_batch(entries, out, o)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        P.stop()
        print("\n⏹ Dihentikan. Jalankan perintah yang sama lagi untuk melanjutkan (file .part akan diteruskan).")
        sys.exit(130)
