"""Tes autentikasi dashboard (tanpa jaringan eksternal, DB sementara).
Jalankan: .venv/bin/python -m unittest discover -s tests -v
"""
import sys, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import ServerCase, Client, USER, PASSWORD  # noqa: E402
from lib import auth, db  # noqa: E402


class AuthBase(ServerCase, unittest.TestCase):
    def setUp(self):
        self.start_server()

    def tearDown(self):
        self.stop_server()


class UnauthenticatedTests(AuthBase):
    def test_halaman_diarahkan_ke_login(self):
        for path in ("/", "/index.html", "/app.js", "/oauth/start", "/callback?code=x&state=y"):
            r = self.client.get(path)
            self.assertEqual(r.status, 302, path)
            self.assertEqual(r.header("Location"), "/login", path)

    def test_api_privat_401(self):
        for path in ("/api/accounts", "/api/posts", "/api/library", "/api/oauth/info",
                     "/api/personas", "/api/post?id=1"):
            r = self.client.get(path)
            self.assertEqual(r.status, 401, path)
            self.assertEqual(r.json(), {"error": "unauthorized"})

    def test_post_privat_401_dan_tidak_menyentuh_db(self):
        with mock.patch.object(db, "new_post") as m:
            r = self.client.post("/api/post", {"handle": "a", "text": "b"})
            self.assertEqual(r.status, 401)
            m.assert_not_called()
        self.assertEqual(self.client.post("/api/upload").status, 401)
        self.assertEqual(self.client.post("/api/auth/logout").status, 401)

    def test_halaman_login_dan_css_publik(self):
        r = self.client.get("/login")
        self.assertEqual(r.status, 200)
        self.assertIn('id="login-form"', r.text)
        self.assertIn("Threads", r.text)
        self.assertEqual(self.client.get("/app.css").status, 200)
        self.assertEqual(self.client.get("/login.js").status, 200)

    def test_me_tanpa_login(self):
        self.assertEqual(self.client.get("/api/auth/me").status, 401)

    def test_header_keamanan(self):
        r = self.client.get("/login")
        self.assertEqual(r.header("X-Frame-Options"), "DENY")
        self.assertEqual(r.header("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.header("Cache-Control"), "no-store")


class LoginTests(AuthBase):
    def test_login_sukses_dan_cookie(self):
        r = self.client.login()
        self.assertEqual(r.status, 200)
        cookie = [v for k, v in r.headers.items() if k.lower() == "set-cookie"][0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Secure", cookie)  # HTTP biasa
        self.assertNotIn(PASSWORD, r.text)
        self.assertEqual(self.client.get("/").status, 200)
        self.assertEqual(self.client.get("/api/accounts").status, 200)
        self.assertEqual(self.client.get("/app.js").status, 200)

    def test_cookie_secure_di_belakang_https_proxy(self):
        r = self.client.login(headers={"X-Forwarded-Proto": "https"})
        cookie = [v for k, v in r.headers.items() if k.lower() == "set-cookie"][0]
        self.assertIn("Secure", cookie)

    def test_login_sudah_login_redirect_dari_halaman_login(self):
        self.client.login()
        r = self.client.get("/login")
        self.assertEqual((r.status, r.header("Location")), (302, "/"))

    def test_kredensial_salah_pesan_generik(self):
        a = self.client.login(password="salah-banget")
        b = self.client.login(username="tidak-ada", password="salah-banget")
        self.assertEqual((a.status, b.status), (401, 401))
        self.assertEqual(a.json(), b.json())  # tidak membedakan user ada/tidak
        self.assertIsNone(self.client.cookie)
        self.assertEqual(self.client.get("/api/accounts").status, 401)

    def test_input_kosong_ditolak(self):
        for body in ({}, {"username": "", "password": ""}, {"username": USER}, {"password": "x"}):
            r = self.client.post("/api/auth/login", body, cookie=False)
            self.assertEqual(r.status, 400, body)

    def test_json_rusak_dan_body_besar(self):
        c = self.client
        self.assertEqual(c.request("POST", "/api/auth/login", cookie=False,
                                   body="x" * 10, headers={}).status, 400)
        self.assertEqual(c.post("/api/auth/login", {"username": "a" * 5000, "password": "b"},
                                cookie=False).status, 413)

    def test_login_origin_asing_ditolak(self):
        r = self.client.login(headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)

    def test_regenerasi_session_tiap_login(self):
        self.client.login()
        old = self.client.cookie
        self.client.login(with_cookie=True)  # kirim cookie lama saat login ulang
        self.assertNotEqual(old, self.client.cookie)
        stale = Client(self.port); stale.cookie = old
        self.assertEqual(stale.get("/api/accounts").status, 401)  # ID lama dicabut
        self.assertEqual(self.client.get("/api/accounts").status, 200)

    def test_password_tersimpan_hash(self):
        con = db.connect()
        h = con.execute("SELECT password_hash FROM admin_users").fetchone()[0]
        con.close()
        self.assertTrue(h.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, h)

    def test_session_id_tidak_tersimpan_plaintext(self):
        self.client.login()
        con = db.connect()
        ids = [r[0] for r in con.execute("SELECT id_hash FROM sessions")]
        con.close()
        self.assertNotIn(self.client.cookie, ids)

    def test_tidak_ada_hash_ke_frontend(self):
        self.client.login()
        for path in ("/api/auth/me", "/api/accounts"):
            self.assertNotIn("scrypt", self.client.get(path).text)

    def test_set_password_validasi_dan_cabut_session(self):
        with self.assertRaises(ValueError):
            auth.set_password("x", "pendek")
        self.client.login()
        auth.set_password(USER, "password-baru-panjang")
        self.assertEqual(self.client.get("/api/accounts").status, 401)
        self.assertEqual(self.client.login().status, 401)
        self.assertEqual(self.client.login(password="password-baru-panjang").status, 200)


class RateLimitTests(AuthBase):
    def test_brute_force_diblokir_meski_password_benar(self):
        for _ in range(auth.MAX_FAIL_PER_USER_IP):
            self.assertEqual(self.client.login(password="salah").status, 401)
        r = self.client.login(password="salah")
        self.assertEqual(r.status, 429)
        self.assertGreater(int(r.header("Retry-After")), 0)
        self.assertEqual(self.client.login().status, 429)  # password benar pun ditolak saat terkunci

    def test_user_lain_dari_ip_sama_tidak_langsung_terkunci(self):
        for _ in range(auth.MAX_FAIL_PER_USER_IP):
            self.client.login(username="x", password="salah")
        self.assertEqual(self.client.login().status, 200)

    def test_batas_per_ip(self):
        for i in range(auth.MAX_FAIL_PER_IP):
            self.client.login(username=f"u{i}", password="salah")
        self.assertEqual(self.client.login().status, 429)

    def test_jendela_kedaluwarsa(self):
        for _ in range(auth.MAX_FAIL_PER_USER_IP):
            self.client.login(password="salah")
        real = time.time
        with mock.patch.object(auth.time, "time", lambda: real() + auth.FAIL_WINDOW + 5):
            self.assertEqual(auth.retry_after("127.0.0.1", USER), 0)

    def test_x_real_ip_dipercaya_dari_proxy_lokal(self):
        h = {"X-Real-IP": "203.0.113.9"}
        for _ in range(auth.MAX_FAIL_PER_USER_IP):
            self.client.login(password="salah", headers=h)
        self.assertEqual(self.client.login(headers=h).status, 429)
        self.assertEqual(self.client.login(headers={"X-Real-IP": "203.0.113.10"}).status, 200)


class CsrfAndSessionTests(AuthBase):
    def setUp(self):
        super().setUp()
        self.client.login()

    def test_post_tanpa_csrf_ditolak(self):
        with mock.patch.object(db, "new_post") as m:
            r = self.client.post("/api/post", {"handle": "a", "text": "b"})
            self.assertEqual(r.status, 403)
            r = self.client.post("/api/post", {"handle": "a", "text": "b"},
                                 headers={"X-CSRF-Token": "salah"})
            self.assertEqual(r.status, 403)
            m.assert_not_called()

    def test_post_dengan_csrf_valid(self):
        tok = self.client.csrf()
        with mock.patch.object(db, "new_post", return_value="p1") as m:
            r = self.client.post("/api/post", {"handle": "a", "text": "b"},
                                 headers={"X-CSRF-Token": tok})
            self.assertEqual((r.status, r.json()["id"]), (200, "p1"))
            m.assert_called_once()

    def test_origin_asing_ditolak_walau_token_benar(self):
        tok = self.client.csrf()
        r = self.client.post("/api/post", {"handle": "a", "text": "b"},
                             headers={"X-CSRF-Token": tok, "Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)

    def test_origin_sama_diterima(self):
        tok = self.client.csrf()
        with mock.patch.object(db, "new_post", return_value="p1"):
            r = self.client.post("/api/post", {"handle": "a", "text": "b"},
                                 headers={"X-CSRF-Token": tok,
                                          "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 200)

    def test_csrf_beda_per_session(self):
        c2 = Client(self.port); c2.login()
        self.assertNotEqual(self.client.csrf(), c2.csrf())
        r = self.client.post("/api/auth/logout", headers={"X-CSRF-Token": c2.csrf()})
        self.assertEqual(r.status, 403)

    def test_logout(self):
        r = self.client.post("/api/auth/logout", headers={"X-CSRF-Token": self.client.csrf()})
        self.assertEqual(r.status, 200)
        cookie = [v for k, v in r.headers.items() if k.lower() == "set-cookie"][0]
        self.assertIn("Max-Age=0", cookie)
        self.assertEqual(self.client.get("/api/accounts").status, 401)  # cookie lama sudah mati di server
        self.assertEqual(self.client.get("/").status, 302)

    def test_session_kedaluwarsa(self):
        self.assertEqual(self.client.get("/api/accounts").status, 200)
        con = db.connect()
        con.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
        con.commit(); con.close()
        self.assertEqual(self.client.get("/api/accounts").status, 401)
        self.assertEqual(self.client.get("/").status, 302)
        self.assertEqual(self.client.get("/api/auth/me").status, 401)
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        con.close()

    def test_session_palsu(self):
        c = Client(self.port); c.cookie = "tebakan-session-id"
        self.assertEqual(c.get("/api/accounts").status, 401)
        c.cookie = "x" * 500
        self.assertEqual(c.get("/api/accounts").status, 401)


class NoAdminTests(unittest.TestCase):
    def test_tanpa_admin_login_gagal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(db, "DB_PATH", Path(d) / "t.db"):
            db.init_db()
            self.assertFalse(auth.has_admin())
            self.assertFalse(auth.verify_login("admin", "apapun-panjang"))


if __name__ == "__main__":
    unittest.main()
