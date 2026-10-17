#!/usr/bin/env python3
"""set_admin.py — buat admin baru / ganti password dashboard.

  .venv/bin/python scripts/set_admin.py <username>

Password diminta lewat prompt (tidak tampil, tidak masuk shell history).
Mengganti password mencabut semua session user itu.
"""
import getpass, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import db, auth  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    db.init_db()
    pw = getpass.getpass("Password baru: ")
    if pw != getpass.getpass("Ulangi password: "):
        print("Password tidak sama.")
        return 1
    try:
        auth.set_password(sys.argv[1], pw)
    except ValueError as e:
        print(f"Gagal: {e}")
        return 1
    print(f"OK: admin '{sys.argv[1].strip().lower()}' tersimpan. Semua session lamanya dicabut.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
