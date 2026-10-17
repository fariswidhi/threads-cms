-- v5: autentikasi dashboard (idempotent, tidak menyentuh tabel lama)
CREATE TABLE IF NOT EXISTS admin_users (
  username      TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,          -- scrypt$n$r$p$salt$hash (base64), bukan plaintext
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id_hash    TEXT PRIMARY KEY,          -- sha256(session id); ID asli hanya ada di cookie
  username   TEXT NOT NULL,
  csrf_token TEXT NOT NULL,
  created_at INTEGER NOT NULL,          -- epoch detik
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at);
