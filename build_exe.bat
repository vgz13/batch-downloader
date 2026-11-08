@echo off
REM ============================================================
REM  Membuat BatchDownloader.exe (Windows). Jalankan di PC Windows:
REM  klik dua kali file ini. Butuh Python 3 dari python.org
REM  (centang "Add python.exe to PATH" saat instal).
REM  Hasil: dist\BatchDownloader.exe
REM ============================================================
cd /d "%~dp0"
py -3 -m pip install --upgrade pip
py -3 -m pip install --upgrade pyinstaller requests yt-dlp keyring
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name BatchDownloader ^
  --icon assets\icon.ico ^
  --add-data "assets\icon_256.png;assets" ^
  --collect-all yt_dlp ^
  --hidden-import keyring.backends.Windows ^
  gui.py
echo.
if exist dist\BatchDownloader.exe (echo Selesai: dist\BatchDownloader.exe) else (echo Build GAGAL, lihat pesan di atas.)
pause
