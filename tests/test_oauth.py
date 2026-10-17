"""Tes alur OAuth Threads (stdlib unittest, tanpa jaringan).
Jalankan: .venv/bin/python -m unittest discover -s tests -v
"""
import json, os, sys, tempfile, threading, time, unittest, urllib.parse, urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import threads_api as t  # noqa: E402

ENV_KEYS = ("APP_BASE_URL", "THREADS_REDIRECT_URI", "THREADS_APP_ID",
            "THREADS_APP_SECRET", "THREADS_SCOPE")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._env = {k: os.environ.pop(k, None) for k in ENV_KEYS}
        # jangan baca .env / app.json asli
        self.patches = [
            mock.patch.object(t, "_load_env", lambda: None),
            mock.patch.object(t, "_app_json", lambda: {}),
            mock.patch.object(t, "TOKENS", self.tmp / "tokens.json"),
        ]
        for p in self.patches:
            p.start()
        t._oauth_states.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def prod(self):
        os.environ.update(APP_BASE_URL="https://cms.larismedia.id/",
                          THREADS_APP_ID="123", THREADS_APP_SECRET="s3cret")


class ConfigTests(Base):
    def test_default_lokal(self):
        cfg = t.oauth_config()
        self.assertEqual(cfg["redirect_uri"], "https://localhost:8456/callback")
        self.assertTrue(cfg["local_mode"])

    def test_redirect_dari_app_base_url(self):
        self.prod()
        cfg = t.oauth_config()
        self.assertEqual(cfg["redirect_uri"], "https://cms.larismedia.id/callback")
        self.assertFalse(cfg["local_mode"])

    def test_redirect_eksplisit_menang(self):
        self.prod()
        os.environ["THREADS_REDIRECT_URI"] = "https://lain.example/callback"
        self.assertEqual(t.oauth_config()["redirect_uri"], "https://lain.example/callback")

    def test_validasi_konfigurasi(self):
        self.assertTrue(any("APP_ID" in p for p in t.oauth_config_problems()))
        self.prod()
        self.assertEqual(t.oauth_config_problems(), [])
        os.environ["THREADS_REDIRECT_URI"] = "http://cms.larismedia.id/callback"
        self.assertTrue(any("HTTPS" in p for p in t.oauth_config_problems()))

    def test_problems_tidak_membocorkan_secret(self):
        self.prod()
        os.environ["THREADS_REDIRECT_URI"] = "http://x/callback"
        self.assertNotIn("s3cret", " ".join(t.oauth_config_problems()))


class StateTests(Base):
    def test_sekali_pakai(self):
        s = t.new_oauth_state()
        self.assertTrue(t.consume_oauth_state(s))
        self.assertFalse(t.consume_oauth_state(s))

    def test_state_asing_atau_kosong(self):
        self.assertFalse(t.consume_oauth_state("palsu"))
        self.assertFalse(t.consume_oauth_state(""))
        self.assertFalse(t.consume_oauth_state(None))

    def test_kedaluwarsa(self):
        s = t.new_oauth_state()
        t._oauth_states[s] = time.monotonic() - 1
        self.assertFalse(t.consume_oauth_state(s))


class FlowTests(Base):
    def test_redirect_uri_sama_di_authorize_dan_exchange(self):
        self.prod()
        url, state = t.oauth_authorize_url()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["redirect_uri"], ["https://cms.larismedia.id/callback"])
        self.assertEqual(q["client_id"], ["123"])
        self.assertEqual(q["state"], [state])
        sent = {}

        def fake_http(u, data=None, method=None):
            if u.endswith("/oauth/access_token"):
                sent.update(urllib.parse.parse_qs(data.decode()))
                return {"access_token": "short"}
            return {"id": "1", "username": "akun"}

        with mock.patch.object(t, "_http", fake_http), \
             mock.patch.object(t, "exchange_long_lived", lambda tok: {"error": "x"}):
            res = t.oauth_exchange_code("CODE")
        self.assertTrue(res["ok"])
        self.assertEqual(sent["redirect_uri"], q["redirect_uri"])
        self.assertEqual(sent["code"], ["CODE"])
        self.assertEqual(json.loads((self.tmp / "tokens.json").read_text()), {"akun": "short"})

    def test_authorize_tanpa_app_id(self):
        url, err = t.oauth_authorize_url()
        self.assertIsNone(url)


class CallbackHTTPTests(Base):
    """Callback lewat server HTTP sungguhan (port acak), exchange & DB di-mock."""

    def setUp(self):
        super().setUp()
        self.prod()
        import server
        self.server = server
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.exch = mock.patch.object(t, "oauth_exchange_code",
                                      return_value={"ok": True, "handle": "akun", "user_id": "1"})
        self.db = mock.patch.object(server.db, "upsert_account")
        self.m_exch = self.exch.start()
        self.m_db = self.db.start()

    def tearDown(self):
        self.exch.stop(); self.db.stop()
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def get(self, path):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        try:
            r = op.open(self.base + path)
            return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def test_start_redirect_ke_meta_dengan_redirect_production(self):
        code, _, h = self.get("/oauth/start")
        self.assertEqual(code, 302)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(h["Location"]).query)
        self.assertEqual(q["redirect_uri"], ["https://cms.larismedia.id/callback"])

    def test_redirect_uri_dari_query_diabaikan(self):
        _, _, h = self.get("/oauth/start?redirect_uri=https://evil.example/cb")
        self.assertNotIn("evil.example", h["Location"])

    def test_callback_tanpa_state_ditolak(self):
        _, body, _ = self.get("/callback?code=abc")
        self.assertIn("State tidak valid", body)
        self.m_exch.assert_not_called()

    def test_callback_state_palsu_ditolak(self):
        _, body, _ = self.get("/callback?code=abc&state=palsu")
        self.assertIn("State tidak valid", body)
        self.m_exch.assert_not_called()

    def test_callback_sukses_strip_fragment_dan_tanpa_kebocoran(self):
        state = t.new_oauth_state()
        _, body, h = self.get(f"/callback?code=abc%23_&state={state}")
        self.assertIn("@akun terhubung", body)
        self.m_exch.assert_called_once_with("abc")
        self.assertNotIn("s3cret", body)
        self.assertEqual(h["Cache-Control"], "no-store")
        # state sekali pakai
        _, body2, _ = self.get(f"/callback?code=abc&state={state}")
        self.assertIn("State tidak valid", body2)

    def test_callback_error_provider_dan_xss_di_escape(self):
        state = t.new_oauth_state()
        _, body, _ = self.get("/callback?" + urllib.parse.urlencode(
            {"error": "access_denied", "state": state,
             "error_description": "<script>alert(1)</script>"}))
        self.assertIn("Ditolak", body)
        self.assertNotIn("<script>alert(1)", body)
        self.m_exch.assert_not_called()

    def test_oauth_info(self):
        _, body, _ = self.get("/api/oauth/info")
        self.assertEqual(json.loads(body), {"start_url": "/oauth/start", "local_mode": False})


if __name__ == "__main__":
    unittest.main()
