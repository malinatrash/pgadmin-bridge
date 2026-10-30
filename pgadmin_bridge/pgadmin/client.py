"""HTTP client for one pgAdmin instance: a pool of logins (lanes), keep-alive connections, server tree, Query Tool calls."""
import gzip
import http.client
import http.cookiejar
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request

from ..log import elapsed, log

RETRY = (http.client.RemoteDisconnected, http.client.BadStatusLine, http.client.CannotSendRequest,
         http.client.ResponseNotReady, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


class ApiError(Exception):
    def __init__(self, msg, tx=0, safe=False):
        super().__init__(msg)
        self.tx, self.safe = tx, safe  # safe: the statement did not run, it can be retried on a new backend


class Lost(ApiError):
    """The Query Tool session is gone; the client has to reconnect (or the session re-attaches when safe)."""


class PgAdmin:
    """One pgAdmin instance. Every lane is a separate login (= Flask session) with its own keep-alive connection.

    pgAdmin keeps Query Tool state in the Flask session and writes the whole session back after each request,
    so requests under one cookie must be serialized; lanes give parallelism without the clobbering.
    """

    def __init__(self, inst, cookie_dir=None, lanes=4):
        self.name, self.base = inst["name"], inst["url"].rstrip("/")
        self.email, self.password = inst["email"], inst["password"]
        self.cookie_dir = cookie_dir
        self.ctx = ssl.create_default_context()
        if not inst.get("verify_tls", True):
            self.ctx.check_hostname, self.ctx.verify_mode = False, ssl.CERT_NONE
        self.fetch_all = None  # None: unknown, True/False: whether /sqleditor/fetch/<tid>/1 exists on this pgAdmin
        self.autocommit_calls = False  # True once a backend came up without autocommit: set it explicitly from then on
        self.lanes = [Lane(self, i) for i in range(max(1, lanes))]
        self.lock = threading.Lock()

    def lane(self, reserve=False):
        """The lane with the fewest Query Tools bound to it; reserve=True counts the caller's tool in right away."""
        with self.lock:
            ln = min(self.lanes, key=lambda ln: ln.tools)
            if reserve:
                ln.tools += 1
            return ln

    def release(self, ln):
        with self.lock:
            ln.tools = max(0, ln.tools - 1)

    def call(self, method, path, body=None):
        """A call that does not belong to a Query Tool: any lane will do."""
        return self.lane().call(method, path, body)

    def servers(self):
        """All server nodes of the tree with their group id; -> [node]."""
        out, ln = [], self.lane()
        for g in ln.call("GET", "/browser/server_group/nodes/")["data"]:
            try:
                nodes = _items(ln.call("GET", f"/browser/server/nodes/{g['_id']}/"))
            except Lost:
                raise
            except ApiError:
                continue
            ln.connected |= {s["_id"] for s in nodes if s.get("connected")}
            out += [dict(s, gid=g["_id"]) for s in nodes]
        return out


class Lane:
    """One pgAdmin login: cookie jar, CSRF token, one keep-alive HTTP connection; calls on a lane are serialized."""

    def __init__(self, pg, index):
        self.pg, self.index = pg, index
        self.lock, self.conn, self.authed, self.tools, self.connected = threading.Lock(), None, False, 0, set()
        self.headers = {"Referer": pg.base + "/browser/", "Accept-Encoding": "gzip"}
        suffix = ".cookies" if index == 0 else f".cookies.{index}"
        self.jar_path = pg.cookie_dir and os.path.join(pg.cookie_dir, _slug(pg.name) + suffix)
        self.jar = http.cookiejar.MozillaCookieJar(self.jar_path)
        if self.jar_path and os.path.exists(self.jar_path):
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except (OSError, http.cookiejar.LoadError):
                pass

    def __repr__(self):
        return f"{self.pg.name}[{self.index}]"

    def call(self, method, path, body=None, safe=False):
        """-> pgAdmin's JSON answer (a dict); ApiError/Lost otherwise. safe marks a call whose statement did not run yet."""
        start = time.monotonic()
        with self.lock:
            try:
                if not self.authed:
                    self._auth()
                st, _, raw = self._request(method, path, json_body=body)
                data = json.loads(raw) if raw[:1] in (b"{", b"[") else None
            except TimeoutError:
                st, data = "timeout", None
            except (OSError, ValueError, http.client.HTTPException) as e:
                st, data = e, None
            if st in (301, 302, 401):
                self.authed = False
        log(f"  http {self} {method} {path} -> {st} {elapsed(start)}")
        if not isinstance(data, dict):
            if isinstance(st, int) and st in (404, 405):
                raise ApiError(f"pgAdmin '{self.pg.name}': HTTP {st} for {path}", safe=safe)
            raise Lost(f"pgAdmin '{self.pg.name}' did not answer ({st}); reconnect", safe=safe)
        if st < 400 and data.get("success") != 0:
            return data
        extra = data.get("data")
        tx = (extra.get("transaction_status") or 0) if isinstance(extra, dict) else 0
        info = data.get("info")
        if info in ("DATAGRID_TRANSACTION_REQUIRED", "CONNECTION_LOST"):
            raise Lost(data.get("errormsg") or f"pgAdmin: HTTP {st}", tx, safe=safe)
        raise ApiError(data.get("errormsg") or f"pgAdmin: HTTP {st}", tx, safe=safe)

    def _auth(self):
        if not self._csrf():
            self._login()
            if not self._csrf():
                raise ApiError(f"pgAdmin '{self.pg.name}': login failed")
        if self.jar_path:
            self.jar.save(ignore_discard=True, ignore_expires=True)
            os.chmod(self.jar_path, 0o600)
        self.authed = True

    def _csrf(self):
        st, _, body = self._request("GET", "/browser/js/utils.js")
        m = re.search(rb"pgAdmin\['csrf_token'\] = '([^']+)'", body) if st == 200 else None
        if m:
            self.headers["X-pgA-CSRFToken"] = m.group(1).decode()
        return bool(m)

    def _login(self):
        log(f"  http {self} logging in as {self.pg.email}")
        self.jar.clear()
        self.connected.clear()
        _, _, body = self._request("GET", "/login")
        m = re.search(rb'csrfToken": "([^"]+)"', body)
        if not m:
            raise ApiError(f"pgAdmin '{self.pg.name}': no csrfToken on the login page (is the URL right?)")
        st, hdr, _ = self._request("POST", "/authenticate/login", form={
            "csrf_token": m.group(1).decode(), "email": self.pg.email, "password": self.pg.password,
            "language": "en", "internal_button": "Login"})
        if st != 302 or "/login" in (hdr.get("Location") or ""):
            raise ApiError(f"pgAdmin '{self.pg.name}': login rejected ({st}); check email/password")

    def _request(self, method, path, json_body=None, form=None, timeout=120):
        """One HTTP exchange on the keep-alive connection; a connection the server dropped while idle is reopened once."""
        url = self.pg.base + path
        h = dict(self.headers)
        body = None
        if json_body is not None:
            body, h["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif form is not None:
            body, h["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, method=method)  # only for the cookie jar's Request/Response protocol
        self.jar.add_cookie_header(req)
        if req.get_header("Cookie"):
            h["Cookie"] = req.get_header("Cookie")
        u = urllib.parse.urlsplit(url)
        target = u.path + ("?" + u.query if u.query else "")
        for attempt in (1, 2):
            conn = self.conn or self._connect(u, timeout)
            try:
                conn.request(method, target, body=body, headers=h)
                resp = conn.getresponse()
                raw = resp.read()
            except RETRY:
                self._drop()
                if attempt == 2:
                    raise
                continue
            except Exception:
                self._drop()
                raise
            self.jar.extract_cookies(resp, req)
            if resp.getheader("Content-Encoding", "").lower() == "gzip" and raw:
                raw = gzip.decompress(raw)
            if resp.version == 10 or resp.getheader("Connection", "").lower() == "close" or resp.will_close:
                self._drop()
            return resp.status, resp.headers, raw

    def _connect(self, u, timeout):
        host, port = u.hostname, u.port
        if u.scheme == "https":
            self.conn = http.client.HTTPSConnection(host, port or 443, timeout=timeout, context=self.pg.ctx)
        else:
            self.conn = http.client.HTTPConnection(host, port or 80, timeout=timeout)
        return self.conn

    def _drop(self):
        conn, self.conn = self.conn, None
        if conn:
            try:
                conn.close()
            except OSError:
                pass


def _items(resp):
    return resp.get("result") or resp.get("data") or []


def _slug(name):
    return re.sub(r"[^\w.-]+", "_", name)
