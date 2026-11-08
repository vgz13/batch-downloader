# Dipakai launcher: cari Python yang punya Tk (≥8.6) + requests.
find_python() {
  local c
  for c in /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
           /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import tkinter,sys; sys.exit(0 if tkinter.TkVersion>=8.6 else 1)' 2>/dev/null || continue
    if ! "$c" -c 'import requests' 2>/dev/null; then
      "$c" -m pip install --user --quiet requests 2>/dev/null || \
      "$c" -m pip install --user --quiet --break-system-packages requests 2>/dev/null || continue
    fi
    # mesin video (yt-dlp) — opsional, tidak menggagalkan launcher
    "$c" -c 'import yt_dlp' 2>/dev/null || "$c" -m pip install --user --quiet yt-dlp 2>/dev/null || \
      "$c" -m pip install --user --quiet --break-system-packages yt-dlp 2>/dev/null
    echo "$c"; return 0
  done
  return 1
}
