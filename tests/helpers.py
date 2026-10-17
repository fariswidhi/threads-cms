"""Helper tes: DB sementara + server HTTP sungguhan di port acak + klien tanpa auto-redirect."""
import http.client, json, sys, tempfile, threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import db, auth  # noqa: E402

USER, PASSWORD = "admin", "correct-horse-battery"


class Resp:
    def __init__(self, r, body):
        self.status, self.headers, self.body = r.status, r.headers, body

    def header(self, name):
        return self.headers.get(name)

    def json(self):
        return json.loads(self.body)

    @property
    def text(self):
        return self.body.decode()


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def request(self, method, path, body=None, headers=None, cookie=True):
        h = dict(headers or {})
        if cookie and self.cookie:
            h["Cookie"] = f"{auth.COOKIE_NAME}={self.cookie}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        resp = Resp(r, r.read())
        c.close()
        return resp

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body if body is not None else {}, **kw)

    def login(self, username=USER, password=PASSWORD, headers=None, with_cookie=False):
        r = self.post("/api/auth/login", {"username": username, "password": password},
                      headers=headers, cookie=with_cookie)
        for k, v in r.headers.items():
            if k.lower() == "set-cookie" and v.startswith(auth.COOKIE_NAME + "="):
                self.cookie = v.split(";")[0].split("=", 1)[1] or None
        return r

    def csrf(self):
        return self.get("/api/auth/me").json()["csrf"]


class ServerCase:
    """Mixin utk unittest.TestCase: DB sementara dengan 1 admin + server HTTP."""

    def start_server(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_patch = mock.patch.object(db, "DB_PATH", Path(self._tmp.name) / "test.db")
        self._db_patch.start()
        db.init_db()
        auth.set_password(USER, PASSWORD)
        auth.reset_rate_limits()
        import server
        self.server = server
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.client = Client(self.port)

    def stop_server(self):
        self.srv.shutdown()
        self.srv.server_close()
        self._db_patch.stop()
        self._tmp.cleanup()
