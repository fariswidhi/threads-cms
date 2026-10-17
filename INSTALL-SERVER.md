# Panduan Install Threads CMS v2 di Server

Panduan ini untuk server Linux (Ubuntu/Debian) memakai systemd. Untuk macOS ada bagian tersendiri di akhir.

## 0. Hal penting sebelum mulai

| Fakta | Dampak |
|---|---|
| Server **hanya listen di `127.0.0.1:8455`** (hardcoded di `server.py`) | Tidak bisa diakses langsung dari internet. Pakai SSH tunnel atau reverse proxy. |
| Dashboard **tidak punya login/auth** | Jangan di-expose ke publik tanpa proteksi (basic auth / VPN / SSH tunnel). Siapa pun yang bisa akses bisa posting dari akunmu. |
| Butuh **Python 3.12 atau lebih lama** | `server.py` memakai modul `cgi` yang dihapus di Python 3.13+. |
| Proses harus **nyala 24/7** | Scheduler (cek tiap 30 detik) ada di dalam proses yang sama. |
| Token akun dibaca dari `~/.threads-bot/tokens.json` | Dipakai user yang menjalankan service. |
| OAuth callback **dikonfigurasi** lewat `.env` | Production: `https://<domain>/callback` lewat Nginx (tanpa port 8456, tanpa sertifikat lokal). Lokal: `https://localhost:8456/callback`. Lihat bagian 5. |

## 1. Prasyarat

- Server Linux dengan akses SSH dan sudo
- Python 3.12 atau 3.11 (**bukan 3.13/3.14**)
- Bucket object storage S3-compatible (misal Cloudflare R2) dengan CDN/domain publik. Threads API hanya menerima gambar lewat URL publik.
- Meta app Threads (App ID dan App Secret) untuk OAuth
- Opsional, untuk fitur AI: endpoint LLM OpenAI-compatible

## 2. Siapkan server

```bash
sudo apt update
sudo apt install -y git python3.12 python3.12-venv   # Ubuntu 24.04. Distro lain: pakai pyenv/uv untuk Python 3.12
python3.12 --version
```

Kalau repo apt tidak punya Python 3.12, alternatifnya pakai [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12 .venv`.

Buat user khusus (disarankan):

```bash
sudo adduser --disabled-password --gecos "" threads
sudo -iu threads
```

## 3. Clone dan install dependency

```bash
cd ~
git clone https://github.com/Faresapn/threads-cms-v2.git
cd threads-cms-v2
python3.12 -m venv .venv
.venv/bin/python -m pip install boto3
```

## 4. Konfigurasi `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

```ini
# Object storage (S3-compatible)
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_MEDIA_BUCKET=nama-bucket
R2_CDN_URL=https://cdn.domainkamu.com
R2_PREFIX=threads

# AI generate (opsional)
AI_BASE_URL=https://api.provider.com/v1
AI_API_KEY=...
AI_MODEL=nama-model

# Threads OAuth (production)
APP_BASE_URL=https://cms.larismedia.id
THREADS_REDIRECT_URI=https://cms.larismedia.id/callback
THREADS_APP_ID=1888584452332615
THREADS_APP_SECRET=isi-app-secret-dari-meta
```

`.env` sudah ada di `.gitignore`. Jangan di-commit.

## 5. Siapkan akun Threads (OAuth)

### 5.1 Konfigurasi OAuth

Semua nilai OAuth dibaca dari `.env` (prioritas tertinggi), lalu `app.json` (kompatibel dengan setup lama), lalu default.

| Variabel | Fungsi |
|---|---|
| `APP_BASE_URL` | URL publik app, mis. `https://cms.larismedia.id`. Kalau `THREADS_REDIRECT_URI` kosong, redirect = `APP_BASE_URL` + `/callback`. |
| `THREADS_REDIRECT_URI` | Redirect URI eksplisit. Harus **persis sama** dengan yang didaftarkan di Meta. |
| `THREADS_APP_ID` | App ID Threads. |
| `THREADS_APP_SECRET` | App Secret. Hanya di `.env` (`chmod 600`), jangan di-commit, tidak pernah ditulis ke log. |
| `THREADS_SCOPE` | Opsional. Default: `threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies,threads_manage_insights`. |

Cara kerja mode:
- **Production**: `APP_BASE_URL` / `THREADS_REDIRECT_URI` diisi dengan host non-localhost. App **tidak** membuka port 8456 dan tidak butuh `tls-*.pem`. TLS ditangani Nginx.
- **Lokal (dev)**: kosongkan keduanya. Callback = `https://localhost:8456/callback`, butuh `tls-cert.pem` dan `tls-key.pem` (lihat 5.4).

Saat start, app mencetak mode dan redirect URI yang dipakai, serta peringatan bila konfigurasi tidak lengkap (tanpa menampilkan secret):

```
[oauth] redirect_uri = https://cms.larismedia.id/callback (production)
```

### 5.2 Daftarkan Redirect URI di Meta Developer

1. Buka [developers.facebook.com](https://developers.facebook.com) > app Threads kamu > **Use cases > Threads API > Settings**.
2. Isi **Redirect Callback URLs** dengan **persis**:
   ```
   https://cms.larismedia.id/callback
   ```
   Meta mencocokkan string secara exact. Beda `http`/`https` atau trailing slash menyebabkan error redirect_uri. Dashboard Meta kadang menambahkan trailing slash sendiri, jadi periksa daftar yang tersimpan.
3. Selama app belum live, akun Threads yang mau dihubungkan harus ditambahkan sebagai **Threads Tester** dan undangannya sudah diterima.
4. App ID ada di halaman yang sama. App Secret ada di **App settings > Basic**.

### 5.3 Menghubungkan akun

**Via dashboard (production):**
1. Buka `https://cms.larismedia.id` (login basic auth bila dipasang).
2. Tab Akun > **Connect Akun Baru**. Popup Meta terbuka, setujui izin.
3. Meta mengarahkan ke `https://cms.larismedia.id/callback`. Halaman menampilkan `@handle terhubung!`, dan token long-lived (60 hari) tersimpan di `~/.threads-bot/tokens.json`.

App memvalidasi parameter `state` (acak, sekali pakai, berlaku 10 menit). Kalau popup dibiarkan lebih dari 10 menit atau callback dibuka ulang, muncul "State tidak valid / kedaluwarsa": ulangi dari tombol Connect.

**Alternatif: salin token dari komputer lain.**

```bash
mkdir -p ~/.threads-bot && chmod 700 ~/.threads-bot
scp ~/.threads-bot/tokens.json threads@IP_SERVER:~/.threads-bot/
ssh threads@IP_SERVER "chmod 600 ~/.threads-bot/tokens.json"
```

Format `tokens.json`: `{"handle_threads": "access_token", ...}`. Kalau ada `queue.json` lama, salin juga supaya antrean ikut ter-migrasi (bagian 6).

### 5.4 Mode lokal (dev) dengan `localhost:8456`

Hanya untuk development di laptop. Kosongkan `APP_BASE_URL` dan `THREADS_REDIRECT_URI`, daftarkan `https://localhost:8456/callback` di Meta, lalu:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout tls-key.pem -out tls-cert.pem -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost"
chmod 600 tls-key.pem
```

Browser akan memperingatkan sertifikat self-signed di `https://localhost:8456`. Lanjutkan saja.

## 6. Inisialisasi database

```bash
cd ~/threads-cms-v2
.venv/bin/python lib/db.py
.venv/bin/python scripts/migrate.py    # import akun dan antrean dari ~/.threads-bot
```

## 7. Jalankan sebagai service (systemd)

Keluar dari user `threads` (`exit`), lalu buat service:

```bash
sudo tee /etc/systemd/system/threads-cms.service >/dev/null <<'EOF'
[Unit]
Description=Threads CMS v2
After=network-online.target
Wants=network-online.target

[Service]
User=threads
WorkingDirectory=/home/threads/threads-cms-v2
ExecStart=/home/threads/threads-cms-v2/.venv/bin/python server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now threads-cms
sudo systemctl status threads-cms
```

Cek:

```bash
curl -s http://127.0.0.1:8455/api/accounts
journalctl -u threads-cms -f      # log
```

Jangan pakai `deploy/install.sh` di Linux. Itu khusus macOS (launchd), dan plist-nya berisi path milik developer asli.

## 8. Nginx + HTTPS (production)

Dashboard **tidak punya login sendiri**, jadi seluruh domain **wajib** diproteksi (basic auth / pembatasan IP). Callback OAuth ikut terlindungi: browser yang menekan Connect sudah membawa kredensial basic auth, dan callback tetap divalidasi lewat `state`.

Pastikan DNS `cms.larismedia.id` mengarah ke IP VPS.

```bash
sudo apt install -y nginx apache2-utils certbot python3-certbot-nginx
sudo htpasswd -c /etc/nginx/.htpasswd admin

sudo tee /etc/nginx/sites-available/threads-cms >/dev/null <<'EOF'
server {
    listen 80;
    server_name cms.larismedia.id;
    client_max_body_size 50m;          # upload gambar

    location / {
        auth_basic "Threads CMS";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:8455;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 120s;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/threads-cms /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d cms.larismedia.id --redirect
```

Hanya port 8455 yang dipakai app, dan ia hanya listen di `127.0.0.1`. Port 8456 **tidak** dipakai di production. Jangan buka keduanya di firewall:

```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

Uji dari luar server:

```bash
curl -I https://cms.larismedia.id/                 # 401 (minta login)
curl -s -u admin:PASSWORD https://cms.larismedia.id/api/oauth/info
# {"start_url": "/oauth/start", "local_mode": false}
```

Lalu hubungkan akun (5.3).

### Alternatif: SSH tunnel (tanpa domain)

```bash
ssh -N -L 8455:127.0.0.1:8455 threads@IP_SERVER
```

Buka `http://localhost:8455`. Untuk OAuth lewat tunnel perlu mode lokal (5.4) dan tunnel port 8456 juga.

## 9. Operasional

| Tugas | Perintah |
|---|---|
| Restart | `sudo systemctl restart threads-cms` |
| Stop | `sudo systemctl stop threads-cms` |
| Log | `journalctl -u threads-cms -f` (app tidak mencetak App Secret/token. Tetap jangan menempel isi `.env` atau `tokens.json` saat minta bantuan.) |
| Update | `cd ~/threads-cms-v2 && git pull && sudo systemctl restart threads-cms` |
| Backup | Salin `db/cms.db`, `.env`, `~/.threads-bot/`. Stop service dulu, atau pakai `sqlite3 db/cms.db ".backup backup.db"`. |

Contoh backup harian (cron user `threads`):

```bash
0 3 * * * sqlite3 ~/threads-cms-v2/db/cms.db ".backup ~/backup/cms-$(date +\%F).db"
```

## 10. Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| `ModuleNotFoundError: No module named 'cgi'` | Python terlalu baru. Pakai venv Python 3.12 atau 3.11. |
| `ModuleNotFoundError: boto3` | Install di venv yang sama dengan yang dipakai service: `.venv/bin/python -m pip install boto3`. |
| Daftar akun kosong (`[]`) | Belum ada akun terhubung. Pakai Connect Akun (5.3), atau salin `tokens.json` ke `~/.threads-bot/` milik user service lalu jalankan ulang `scripts/migrate.py`. |
| `[oauth] cert TLS gak ada, OAuth HTTPS nonaktif` | Hanya muncul di mode lokal. Buat sertifikat (5.4), atau set `APP_BASE_URL` untuk mode production (pesan ini hilang). |
| Meta: *redirect_uri* tidak cocok / invalid | Redirect URI di Meta tidak sama persis dengan yang dipakai app. Lihat nilai di log start (`[oauth] redirect_uri = ...`), samakan dengan Redirect Callback URLs di Meta (http vs https, trailing slash, domain). Setelah mengubah `.env`, restart service. |
| Callback: *State tidak valid / kedaluwarsa* | Popup dibuka lebih dari 10 menit, callback di-refresh, atau service restart di tengah proses (state ada di memori). Ulangi dari tombol Connect Akun Baru. |
| Callback: *tuker code gagal* | `THREADS_APP_SECRET` salah, kode sudah dipakai atau lebih dari 1 jam, atau `redirect_uri` berbeda. Cek peringatan di log startup. |
| Log start: `PERINGATAN: THREADS_APP_SECRET belum di-set` | Isi `THREADS_APP_SECRET` di `.env` (atau `app_secret` di `app.json`), lalu restart. |
| Connect Akun membuka `https://localhost:8456` di production | `APP_BASE_URL`/`THREADS_REDIRECT_URI` belum terbaca. Pastikan `.env` ada di folder app dan service direstart. |
| Akun tidak bisa authorize | Akun belum jadi Threads Tester di app Meta (app masih mode development). |
| `token @handle gak ada` | Handle tidak ada di `tokens.json`. Ulangi OAuth atau salin tokennya. |
| Upload gambar gagal | Cek kredensial `R2_*` di `.env`, dan pastikan `R2_CDN_URL` bisa diakses publik. |
| AI generate error | Cek `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`. Default `AI_BASE_URL` adalah `http://localhost:8000/v1`. |
| Port 8455 sudah dipakai | `sudo ss -ltnp \| grep 8455`. Port hardcoded di `server.py` (`PORT = 8455`). |
| Post terjadwal tidak terkirim | Pastikan service aktif 24/7 dan zona waktu server sesuai (`timedatectl`). |
| Token Threads kedaluwarsa | Token long-lived berlaku sekitar 60 hari. Ada endpoint refresh di dashboard. Pantau berkala. |

## 11. Menjalankan tes

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tes OAuth tidak memakai jaringan dan tidak menyentuh `.env`, `tokens.json`, atau database asli.

## 12. Install di macOS (launchd)

```bash
git clone https://github.com/Faresapn/threads-cms-v2.git && cd threads-cms-v2
python3.12 -m venv .venv && .venv/bin/python -m pip install boto3
cp .env.example .env     # isi kredensial
.venv/bin/python lib/db.py && .venv/bin/python scripts/migrate.py
```

`deploy/com.branko.threads-cms-v2.plist` berisi path hardcoded milik developer asli (`/Users/brankosutisyo/...`). **Edit dulu** sebelum `bash deploy/install.sh`:
- `ProgramArguments`: ubah ke `<path-kamu>/threads-cms-v2/.venv/bin/python` dan `<path-kamu>/threads-cms-v2/server.py`
- `WorkingDirectory`, `StandardOutPath`, `StandardErrorPath`: ubah ke folder app kamu
- `PATH`: sesuaikan home directory

Perintah cek/stop ada di output `install.sh`.

## Checklist cepat

- [ ] Python 3.12 / 3.11 terpasang
- [ ] Repo di-clone, venv dibuat, `boto3` terinstall
- [ ] `.env` terisi (R2, dan AI bila perlu)
- [ ] `.env` berisi `APP_BASE_URL`, `THREADS_REDIRECT_URI`, `THREADS_APP_ID`, `THREADS_APP_SECRET`
- [ ] Redirect Callback URL di Meta = `THREADS_REDIRECT_URI` (persis)
- [ ] Akun terhubung lewat Connect Akun (atau `tokens.json` disalin)
- [ ] `lib/db.py` dan `scripts/migrate.py` dijalankan
- [ ] Service systemd aktif, `curl 127.0.0.1:8455/api/accounts` merespons
- [ ] Nginx + HTTPS + basic auth aktif; port 8455/8456 tidak dibuka di firewall
