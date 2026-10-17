#!/usr/bin/env python3
"""auth.py — login admin + session untuk Threads CMS v2 (stdlib saja).

- Password: scrypt + salt acak (hashlib.scrypt), tersimpan di tabel admin_users.
- Session: ID acak di cookie HttpOnly; yang tersimpan di DB hanya sha256-nya.
- Tiap session punya token CSRF sendiri (dikirim lewat header X-CSRF-Token).
- Rate limit login di memori (per IP dan per IP+username).
Tidak ada password/hash/session ID yang pernah di-log.
"""
import base64, hashlib, hmac, os, secrets, threading, time
from lib import db

COOKIE_NAME = "cms_session"
SESSION_TTL = int(os.environ.get("SESSION_TTL_HOURS", "12")) * 3600
MIN_PASSWORD_LEN = 10
_SCRYPT = (2 ** 14, 8, 1)  # n, r, p  (~16MB, aman untuk VPS kecil)

# rate limit login
MAX_FAIL_PER_USER_IP = 5
MAX_FAIL_PER_IP = 20
FAIL_WINDOW = 15 * 60


# ── password ──────────────────────────────────────────────────────────────
def hash_password(password):
    n, r, p = _SCRYPT
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${n}${r}${p}${b(salt)}${b(dk)}"


def _verify_hash(password, stored):
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                              n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def set_password(username, password):
    """Buat admin baru atau ganti password. Semua session user itu dicabut."""
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("username kosong")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password minimal {MIN_PASSWORD_LEN} karakter")
    now = db.now_iso()
    con = db.connect()
    con.execute(
        """INSERT INTO admin_users(username, password_hash, created_at, updated_at)
           VALUES(?,?,?,?)
           ON CONFLICT(username) DO UPDATE SET
             password_hash=excluded.password_hash, updated_at=excluded.updated_at""",
        (username, hash_password(password), now, now))
    con.execute("DELETE FROM sessions WHERE username=?", (username,))
    con.commit()
    con.close()


def verify_login(username, password):
    """True kalau kredensial valid. Waktu proses relatif sama utk user ada/tidak ada."""
    username = (username or "").strip().lower()
    con = db.connect()
    row = con.execute("SELECT password_hash FROM admin_users WHERE username=?",
                      (username,)).fetchone()
    con.close()
    ok = _verify_hash(password or "", row["password_hash"] if row else _DUMMY_HASH)
    return bool(row) and ok


def has_admin():
    con = db.connect()
    n = con.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    con.close()
    return n > 0


# ── session ───────────────────────────────────────────────────────────────
def _hash_id(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


def create_session(username):
    """Selalu buat ID baru (regenerasi saat login). Return (session_id, csrf_token)."""
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = int(time.time())
    con = db.connect()
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    con.execute("INSERT INTO sessions(id_hash, username, csrf_token, created_at, expires_at)"
                " VALUES(?,?,?,?,?)", (_hash_id(sid), username, csrf, now, now + SESSION_TTL))
    con.commit()
    con.close()
    return sid, csrf


def get_session(sid):
    """Return {'username','csrf'} atau None (tidak ada / kedaluwarsa)."""
    if not sid or len(sid) > 200:
        return None
    con = db.connect()
    row = con.execute("SELECT username, csrf_token, expires_at FROM sessions WHERE id_hash=?",
                      (_hash_id(sid),)).fetchone()
    if row and row["expires_at"] < time.time():
        con.execute("DELETE FROM sessions WHERE id_hash=?", (_hash_id(sid),))
        con.commit()
        row = None
    con.close()
    return {"username": row["username"], "csrf": row["csrf_token"]} if row else None


def destroy_session(sid):
    if not sid:
        return
    con = db.connect()
    con.execute("DELETE FROM sessions WHERE id_hash=?", (_hash_id(sid),))
    con.commit()
    con.close()


# ── rate limit ────────────────────────────────────────────────────────────
_fails = {}  # key -> [timestamp, ...]
_fails_lock = threading.Lock()


def _recent(key, now):
    lst = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
    if lst:
        _fails[key] = lst
    else:
        _fails.pop(key, None)
    return lst


def retry_after(ip, username):
    """Detik sampai boleh mencoba lagi; 0 = boleh."""
    username = (username or "").strip().lower()[:100]
    now = time.time()
    wait = 0
    with _fails_lock:
        for key, limit in ((("ip", ip), MAX_FAIL_PER_IP),
                           (("ipu", ip, username), MAX_FAIL_PER_USER_IP)):
            lst = _recent(key, now)
            if len(lst) >= limit:
                wait = max(wait, int(FAIL_WINDOW - (now - lst[0])) + 1)
    return wait


def record_failure(ip, username):
    username = (username or "").strip().lower()[:100]
    now = time.time()
    with _fails_lock:
        for key in (("ip", ip), ("ipu", ip, username)):
            _recent(key, now)
            _fails.setdefault(key, []).append(now)


def record_success(ip, username):
    username = (username or "").strip().lower()[:100]
    with _fails_lock:
        _fails.pop(("ipu", ip, username), None)


def reset_rate_limits():  # untuk tes
    with _fails_lock:
        _fails.clear()
