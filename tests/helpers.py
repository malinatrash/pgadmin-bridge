"""Shared fixtures: a minimal PG wire client and a proxy + web UI wired to a fake pgAdmin."""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
os.environ["PGADMIN_BRIDGE_DATA"] = tempfile.mkdtemp()

from pgadmin_bridge import config, proxy, web  # noqa: E402

from fake_pgadmin import Fake  # noqa: E402

SERVERS = [{"_id": 7, "label": "main", "db": "postgres", "connected": True},
           {"_id": 8, "label": "other", "db": "postgres", "connected": False}]


def pack(kind, body):
    return kind + struct.pack("!i", len(body) + 4) + body


class Client:
    """Minimal PG wire client using the simple query protocol."""

    def __init__(self, port, user, db, password):
        self.s = socket.create_connection(("127.0.0.1", port))
        self.s.settimeout(10)
        params = f"user\0{user}\0database\0{db}\0\0".encode()
        self.s.sendall(struct.pack("!ii", len(params) + 8, 196608) + params)
        kind, body = self.msg()
        if kind == b"E":
            raise ConnectionError(self.err(body))
        self.s.sendall(pack(b"p", password.encode() + b"\0"))
        self.params, self.key = {}, None
        while True:
            kind, body = self.msg()
            if kind == b"E":
                raise ConnectionError(self.err(body))
            if kind == b"S":
                k, v = body.split(b"\0")[:2]
                self.params[k.decode()] = v.decode()
            if kind == b"K":
                self.key = struct.unpack("!ii", body)
            if kind == b"Z":
                break

    def msg(self):
        hdr = self.exactly(5)
        return hdr[:1], self.exactly(struct.unpack("!i", hdr[1:])[0] - 4)

    def exactly(self, n):
        out = b""
        while len(out) < n:
            chunk = self.s.recv(n - len(out))
            if not chunk:
                raise ConnectionError("closed")
            out += chunk
        return out

    def until_ready(self):
        out = []
        while True:
            kind, body = self.msg()
            out.append((kind, body))
            if kind == b"Z":
                return out

    def query(self, sql):
        """-> (kinds, tx status, rows as lists of str|None, error message or None)."""
        self.s.sendall(pack(b"Q", sql.encode() + b"\0"))
        msgs = self.until_ready()
        rows, err = [], None
        for kind, body in msgs:
            if kind == b"D":
                n, pos, row = struct.unpack("!h", body[:2])[0], 2, []
                for _ in range(n):
                    size = struct.unpack("!i", body[pos:pos + 4])[0]
                    pos += 4
                    row.append(None if size < 0 else body[pos:pos + size].decode())
                    pos += max(size, 0)
                rows.append(row)
            if kind == b"E":
                err = self.err(body)
        return "".join(k.decode() for k, _ in msgs), msgs[-1][1].decode(), rows, err

    def err(self, body):
        return {f[0:1].decode(): f[1:].decode() for f in body.split(b"\0") if f}

    def close(self):
        self.s.sendall(pack(b"X", b""))
        self.s.close()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = Fake(SERVERS)
        cfg = dict(config.DEFAULT, password="pw", timeout=300, idle=600, lanes=2, instances=[
            {"name": cls.__name__, "url": cls.fake.url, "email": "admin@example.com", "password": "secret", "verify_tls": True}])
        cls.srv = proxy.Server(("127.0.0.1", 0), cfg, config.DATA)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.ui = web.Server(("127.0.0.1", 0), cls.srv)
        cls.ui_port = cls.ui.server_address[1]
        threading.Thread(target=cls.ui.serve_forever, daemon=True).start()

    def connect(self, user="main", db="app", password="pw"):
        return Client(self.port, user, db, password)

    def http(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.ui_port}{path}", method="POST" if body else "GET",
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
        return json.loads(raw) if path.startswith("/api") else raw.decode()
