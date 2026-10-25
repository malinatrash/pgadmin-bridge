"""HTTP client for one pgAdmin instance: login, server tree, Query Tool endpoints."""
import http.cookiejar
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..log import elapsed, log


class ApiError(Exception):
    def __init__(self, msg, tx=0):
        super().__init__(msg)
        self.tx = tx


class Lost(ApiError):
    """The Query Tool session is gone; the client has to reconnect."""


class PgAdmin:
    """One pgAdmin login shared by all connections; calls are serialized, pgAdmin mixes up transactions otherwise."""

    def __init__(self, inst, cookie_dir=None):
        self.name, self.base = inst["name"], inst["url"].rstrip("/")
        self.email, self.password = inst["email"], inst["password"]
        self.jar_path = cookie_dir and os.path.join(cookie_dir, _slug(self.name) + ".cookies")
        self.jar = http.cookiejar.MozillaCookieJar(self.jar_path)
        if self.jar_path and os.path.exists(self.jar_path):
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except (OSError, http.cookiejar.LoadError):
                pass
        ctx = ssl.create_default_context()
        if not inst.get("verify_tls", True):
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), urllib.request.HTTPSHandler(context=ctx), _NoRedirect())
        self.headers = {"Referer": self.base + "/browser/"}
        self.lock = threading.Lock()
        self.authed = False

    def call(self, method, path, body=None):
        start = time.monotonic()
        with self.lock:
            try:
                if not self.authed:
                    self._auth()
                st, _, raw = self._request(method, path, json_body=body)
                data = json.loads(raw) if st != 401 else None
            except (OSError, ValueError) as e:
                st, data = e, None
            if not isinstance(data, dict):
                self.authed = False
        log(f"  http {self.name} {method} {path} -> {st} {elapsed(start)}")
        if not isinstance(data, dict):
            raise Lost(f"pgAdmin '{self.name}' did not answer ({st}); reconnect")
        if st < 400 and data.get("success") != 0:
            return data
        extra = data.get("data")
        tx = (extra.get("transaction_status") or 0) if isinstance(extra, dict) else 0
        err = Lost if data.get("info") in ("DATAGRID_TRANSACTION_REQUIRED", "CONNECTION_LOST") else ApiError
        raise err(data.get("errormsg") or f"pgAdmin: HTTP {st}", tx)

    def servers(self):
        """All server nodes of the tree with their group id; -> [node]."""
        out = []
        for g in self.call("GET", "/browser/server_group/nodes/")["data"]:
            try:
                nodes = _items(self.call("GET", f"/browser/server/nodes/{g['_id']}/"))
            except Lost:
                raise
            except ApiError:
                continue
            out += [dict(s, gid=g["_id"]) for s in nodes]
        return out

    def _auth(self):
        if not self._csrf():
            self._login()
            if not self._csrf():
                raise ApiError(f"pgAdmin '{self.name}': login failed")
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
        log(f"  http {self.name} logging in as {self.email}")
        self.jar.clear()
        _, _, body = self._request("GET", "/login")
        m = re.search(rb'csrfToken": "([^"]+)"', body)
        if not m:
            raise ApiError(f"pgAdmin '{self.name}': no csrfToken on the login page (is the URL right?)")
        st, hdr, _ = self._request("POST", "/authenticate/login", form={
            "csrf_token": m.group(1).decode(), "email": self.email, "password": self.password,
            "language": "en", "internal_button": "Login"})
        if st != 302 or "/login" in (hdr.get("Location") or ""):
            raise ApiError(f"pgAdmin '{self.name}': login rejected ({st}); check email/password")

    def _request(self, method, path, json_body=None, form=None, timeout=120):
        h = dict(self.headers)
        body = None
        if json_body is not None:
            body, h["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif form is not None:
            body, h["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def _items(resp):
    return resp.get("result") or resp.get("data") or []


def _slug(name):
    return re.sub(r"[^\w.-]+", "_", name)
