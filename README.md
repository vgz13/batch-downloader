# Batch Downloader

Unduh banyak aset sekaligus dari **Google Drive, Dropbox, OneDrive/SharePoint, MediaFire, MEGA**, dan link langsung (http/https). Struktur folder dari sumber ikut dibuat ulang di komputer kamu.

## Instalasi di Mac
Aplikasi sudah terpasang di **/Applications/Batch Downloader.app** dan bisa dibuka dari Launchpad, Spotlight, atau Finder → Applications. Versi ini mandiri: semua kodenya ada di dalam `.app`, jadi tetap jalan walaupun SSD ini dicabut.

Kalau kode di folder ini diubah, perbarui versi yang terpasang dengan:
```bash
./install.sh
```

## Tampilan desktop (paling gampang)
Klik dua kali **`Batch Downloader.app`** (atau `Jalankan Downloader.command`). Kamu boleh menyeret `.app` ke Dock, tapi **jangan memindahkannya keluar dari folder ini** karena aplikasi itu menjalankan `gui.py` yang ada di sebelahnya.

Isi jendela, dari atas ke bawah:
1. **Link download**: kotak untuk menempel banyak link, satu per baris (`URL | subfolder` opsional). Ada tombol *Tempel dari Clipboard*, *Impor dari file .txt…*, dan *Bersihkan*.
2. **Simpan ke folder**: ketik path atau klik *Pilih Folder…*. Tombol *Buka di Finder* membuka folder tersebut.
3. **Pengaturan**: jumlah download paralel, jumlah coba ulang, simpan ZIP Dropbox, Google API key, dan cookies.txt (semuanya opsional).
4. **Cek Isi Link**: menampilkan daftar file dan susunan foldernya tanpa mengunduh. **Mulai Download** dan **Stop** (bisa dilanjutkan lagi nanti).
5. Progress bar, kecepatan, file yang sedang diunduh, dan log berwarna. Notifikasi macOS muncul saat selesai. Kalau ada yang gagal, daftarnya bisa langsung dimuat ulang ke kotak link.

Link, folder, dan pengaturan terakhir otomatis diingat di `~/Library/Application Support/BatchDownloader/settings.json`.

**Google API key** disimpan otomatis dan permanen di **Keychain macOS** (aman, tidak berupa teks biasa) setiap kali kolomnya diubah. Key itu juga otomatis dipakai oleh mode Terminal. Untuk menghapusnya, kosongkan kolomnya di aplikasi.

## Video: YouTube, Instagram, Facebook, TikTok, X, dll.
Tempel link video di kotak yang sama dengan link Drive/Dropbox. Aplikasi otomatis mengenali link dari **±1800 situs** yang didukung [yt-dlp](https://github.com/yt-dlp/yt-dlp), mesin yang dipakai di balik layar.

- **Kualitas**: Terbaik, 1080p, 720p, 480p, atau **Audio saja (MP3)**. Kualitas tertinggi dan MP3 butuh `ffmpeg` (sudah terpasang: `brew install ffmpeg`).
- **Playlist / channel / album** otomatis menjadi subfolder, misalnya `Tujuan/<Nama Playlist>/<Judul> [id].mp4`.
- **Instagram, Facebook, video privat, atau yang dibatasi umur**: pilih **Login dari browser** (Chrome/Safari/Firefox/…) yang sudah login ke situs itu. Login dibaca langsung dari browser di Mac ini dan tidak dikirim ke tempat lain. Untuk Safari, macOS mungkin meminta izin *Full Disk Access*.
- Video yang sudah pernah diunduh dicatat di `.video-archive.txt` (atau `.audio-archive.txt`) di folder tujuan, jadi dilewati saat dijalankan ulang.
- Situs sering berubah. Kalau tiba-tiba gagal, klik **Update mesin video**, lalu buka ulang aplikasi.
- Unduh hanya konten milikmu sendiri atau yang memang boleh diunduh. Banyak situs melarang pengunduhan di syarat layanannya.

Terminal: `python3 downloader.py "https://youtu.be/XXXX" -q 720` · `-q audio` untuk MP3 · `--cookies-browser chrome`.

## Lewat Terminal
```bash
python3 downloader.py "https://drive.google.com/drive/folders/XXXX" -o ~/Downloads/Aset
python3 downloader.py -i links.txt --pick          # pilih folder via Finder
python3 downloader.py -i links.txt -o /Volumes/SSD/Aset --dry-run   # lihat daftar file dulu
```

### Format `links.txt`
```
https://drive.google.com/drive/folders/XXXX
https://drive.google.com/file/d/XXXX/view | Font
https://www.dropbox.com/scl/fo/XXXX/XXXX?rlkey=XXXX | Mockup/2024
```
Bagian `| subfolder` bersifat opsional dan menaruh hasilnya di subfolder tersebut.

## Hasil struktur folder
| Sumber | Hasil |
|---|---|
| Folder Google Drive | `Tujuan/<Nama Folder>/<subfolder…>/file`. Folder kosong juga ikut dibuat. |
| Google Docs/Sheets/Slides | Diekspor ke `.docx` / `.xlsx` / `.pptx` |
| Folder Dropbox | Diunduh sebagai ZIP, lalu otomatis diekstrak ke `Tujuan/<Nama Folder>/…` (ZIP dihapus, kecuali pakai `--keep-zip`) |
| Folder MediaFire | Struktur subfolder dibaca lewat API publik MediaFire |
| MEGA | Memakai `megatools` (`brew install megatools`), struktur ikut terjaga |
| File tunggal / link langsung | `Tujuan/<nama file>` |

## Soal "limit"
Google Drive membatasi berapa kali sebuah file publik boleh diunduh ("Download quota exceeded"). Batas ini diatur dari server Google, jadi **tidak ada cara untuk menghilangkannya sepenuhnya**. Yang dilakukan program ini untuk mengurangi dampaknya:

- **Throttle**: ada jeda antar-request ke Drive (`--gdrive-delay`, default 1 detik) supaya tidak dianggap spam.
- **Retry otomatis dengan backoff**: kalau kena kuota, program menunggu 60 dtk → 120 → 240 … (maks. 15 menit) lalu mencoba lagi (`--retries`, `--quota-wait`, `--max-wait`).
- **Resume**: file yang sudah selesai dilewati, file setengah jalan dilanjutkan dari `.part`. Jalankan ulang perintah yang sama kapan saja.
- **`failed_links.txt`**: file yang tetap gagal dicatat di folder tujuan. Jalankan ulang nanti, karena kuota biasanya reset dalam ≤24 jam:
  `python3 downloader.py -i "<tujuan>/failed_links.txt" -o "<tujuan>"`
- **`--cookies cookies.txt`** (opsional): unduh sebagai akun Google kamu sendiri, sehingga yang dipakai kuota per-akun dan file yang dibagikan khusus ke akunmu juga bisa diunduh. Ekspor cookies dari browser memakai ekstensi "Get cookies.txt LOCALLY" (format Netscape). **Jangan bagikan file ini ke siapa pun**, karena isinya setara dengan login akunmu.
- **`--gdrive-api-key KEY`** (opsional, atau env `GDRIVE_API_KEY`): listing folder lewat Drive API (tanpa batas jumlah item, lengkap dengan ukuran file), dan saat kuota web habis program otomatis mencoba lewat jalur API. Buat key gratis di Google Cloud Console → APIs & Services → aktifkan *Google Drive API* → Credentials → API key.
- **Trik manual**: untuk file yang terus kena kuota, buka di browser lalu pilih *Make a copy* / *Add shortcut* ke Drive kamu sendiri, kemudian unduh salinannya.

## Opsi lengkap
```
-o, --out DIR          folder tujuan
--pick                 pilih folder tujuan via Finder
-i, --input FILE       file daftar link
-w, --workers N        download paralel (default 3; untuk Drive jangan terlalu tinggi)
--retries N            percobaan ulang per file (default 6)
--quota-wait S         jeda awal saat kena kuota (default 60)
--max-wait S           jeda maksimum (default 900)
--gdrive-delay S       jeda antar request Drive (default 1.0)
--gdrive-api-key KEY   Google API key
--cookies FILE         cookies.txt akun Google
--keep-zip             simpan ZIP Dropbox setelah diekstrak
--dry-run              hanya tampilkan daftar file
```
Tekan `Ctrl+C` untuk berhenti. Saat dijalankan lagi, unduhan dilanjutkan dari posisi terakhir.

## Keterbatasan
- Hanya bisa mengunduh link yang **publik/dibagikan** (atau yang bisa diakses akunmu lewat `--cookies`).
- Dukungan OneDrive bersifat *best-effort*: folder OneDrive pribadi kadang butuh login dari Microsoft.
- Folder Drive yang sangat besar (ribuan file) lebih aman memakai `--gdrive-api-key`.
