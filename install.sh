#!/bin/zsh
# Pasang Batch Downloader sebagai aplikasi mandiri di /Applications.
# Semua kode disalin ke dalam .app, jadi aplikasi tetap jalan walaupun folder/SSD ini dicabut.
# Jalankan ulang skrip ini setelah mengubah kode untuk memperbarui versi yang terpasang.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-/Applications}"
[ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"
APPDIR="$DEST/Batch Downloader.app"
TMP="$(mktemp -d)/Batch Downloader.app"

mkdir -p "$TMP/Contents/MacOS" "$TMP/Contents/Resources/app/assets"
cp "$SRC/downloader.py" "$SRC/gui.py" "$SRC/find_python.sh" "$TMP/Contents/Resources/app/"
cp "$SRC/assets/icon_256.png" "$TMP/Contents/Resources/app/assets/"
cp "$SRC/assets/AppIcon.icns" "$TMP/Contents/Resources/AppIcon.icns"

cat > "$TMP/Contents/MacOS/BatchDownloader" <<'EOF'
#!/bin/zsh
APP="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
source "$APP/find_python.sh"
PY=$(find_python) || { osascript -e 'display alert "Batch Downloader" message "Python dengan Tk tidak ditemukan. Install Python dari python.org lalu buka lagi."'; exit 1; }
cd "$HOME"
exec "$PY" "$APP/gui.py"
EOF
chmod +x "$TMP/Contents/MacOS/BatchDownloader"

cat > "$TMP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Batch Downloader</string>
  <key>CFBundleDisplayName</key><string>Batch Downloader</string>
  <key>CFBundleIdentifier</key><string>local.batchdownloader</string>
  <key>CFBundleExecutable</key><string>BatchDownloader</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

rm -rf "$APPDIR"
mv "$TMP" "$APPDIR"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APPDIR" 2>/dev/null || true
echo "✓ Terpasang di: $APPDIR"
