#!/bin/zsh
# Membuat installer .dmg: buka DMG → seret "Batch Downloader" ke folder Applications.
# Hasil: dist/BatchDownloader-<versi>.dmg
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
VER="1.1"
STAGE="$(mktemp -d)/Batch Downloader"
mkdir -p "$STAGE" "$SRC/dist"

# 1. bangun .app mandiri ke folder staging (pakai install.sh yang sama)
"$SRC/install.sh" "$STAGE" >/dev/null
codesign --force --deep -s - "$STAGE/Batch Downloader.app" 2>/dev/null || true   # tanda tangan lokal (ad-hoc)

# 2. pintasan ke Applications + petunjuk
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/Baca Saya.txt" <<'EOF'
BATCH DOWNLOADER — cara pasang
==============================
1. Seret "Batch Downloader" ke folder "Applications" di jendela ini.
2. Buka dari Launchpad / Spotlight.
   Pertama kali dibuka di Mac lain, macOS mungkin memblokir karena aplikasi tidak
   ditandatangani Apple: klik kanan ikon → Open → Open. Atau buka System Settings →
   Privacy & Security → "Open Anyway".

Kebutuhan:
• Python 3 dari https://www.python.org/downloads/ (wajib; sudah termasuk tampilan Tk).
  Library 'requests' dan 'yt-dlp' dipasang otomatis saat aplikasi pertama dibuka.
• ffmpeg (opsional, untuk video kualitas tertinggi & MP3):  brew install ffmpeg

Pengaturan & API key tersimpan di Mac masing-masing (Keychain), tidak ikut di installer ini.
EOF

# 3. buat DMG terkompresi
OUT="$SRC/dist/BatchDownloader-$VER.dmg"
rm -f "$OUT"
hdiutil create -volname "Batch Downloader" -srcfolder "$STAGE" -ov -format UDZO "$OUT" >/dev/null
rm -rf "$(dirname "$STAGE")"
echo "✓ Installer dibuat: $OUT ($(du -h "$OUT" | cut -f1))"
