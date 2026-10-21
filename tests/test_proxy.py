"""End-to-end: a raw wire client (and psql when installed) through the proxy into the fake pgAdmin."""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
os.environ["PGPROXY_DATA"] = tempfile.mkdtemp()

import config  # noqa: E402
import proxy  # noqa: E402
import web  # noqa: E402
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


class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = Fake(SERVERS)
        cfg = dict(config.DEFAULT, password="pw", timeout=300, idle=600, instances=[
            {"name": "test", "url": cls.fake.url, "email": "admin@example.com", "password": "secret", "verify_tls": True}])
        cls.srv = proxy.Server(("127.0.0.1", 0), cfg, config.DATA)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.ui = web.Server(("127.0.0.1", 0), cls.srv)
        cls.ui_port = cls.ui.server_address[1]
        threading.Thread(target=cls.ui.serve_forever, daemon=True).start()

    def connect(self, user="main", db="app", password="pw"):
        return Client(self.port, user, db, password)

    def test_login_and_parameters(self):
        c = self.connect()
        self.assertEqual(c.params["server_version"], "16.4")
        self.assertEqual(self.fake.logins, 1)
        c.close()

    def test_rows_types_and_errors(self):
        c = self.connect()
        kinds, tx, rows, err = c.query("select 1")
        self.assertEqual((kinds, tx, rows), ("TDDCZ", "I", [["1", "t", None], ["2", "f", "x"]]))
        self.assertEqual(c.query("select empty")[2], [])
        self.assertEqual(c.query("update t set x = 1")[0], "CZ")
        self.assertIn("N", c.query("do $$ begin end $$")[0])
        self.assertEqual(len(c.query("select many")[2]), 1500)
        kinds, tx, rows, err = c.query("select * from nope")
        self.assertEqual((kinds, err["C"], err["P"]), ("EZ", "42P01", "15"))
        self.assertEqual(c.query("")[0], "IZ")
        c.close()

    def test_transaction_status(self):
        c = self.connect()
        self.assertEqual(c.query("begin")[1], "T")
        self.assertEqual(c.query("select * from nope")[1], "E")
        self.assertEqual(c.query("rollback")[1], "I")
        c.close()

    def test_extended_protocol_is_rejected_once(self):
        c = self.connect()
        c.s.sendall(pack(b"P", b"\0select 1\0\0\0") + pack(b"B", b"\0\0" + b"\0" * 6) + pack(b"E", b"\0" * 5) + pack(b"S", b""))
        kinds = "".join(k.decode() for k, _ in c.until_ready())
        self.assertEqual(kinds, "EZ")
        self.assertEqual(c.query("select 1")[0], "TDDCZ")
        c.close()

    def test_bad_password_server_and_instance_prefix(self):
        with self.assertRaises(ConnectionError) as e:
            self.connect(password="nope")
        self.assertEqual(e.exception.args[0]["C"], "28P01")
        with self.assertRaises(ConnectionError) as e:
            self.connect(user="missing")
        self.assertIn("test/main", e.exception.args[0]["M"])
        c = self.connect(user="test/other", db="")
        self.assertIn(("POST", "/browser/server/connect/1/8", {}), self.fake.calls)
        c.close()

    def test_idle_release_replays_sets(self):
        c = self.connect()
        c.query("SET search_path TO app")
        c.query("SET application_name = 'a'")
        c.query("SET application_name = 'b'")
        conn = next(iter(self.srv.conns.values()))
        self.assertEqual(conn.sets, ["SET search_path TO app", "SET application_name = 'b'"])
        with conn.lock:
            conn.detach("test")
        before = len(self.fake.calls)
        self.assertEqual(c.query("select 1")[0], "TDDCZ")
        starts = [b["sql"] for m, p, b in self.fake.calls[before:] if "query_tool/start" in p]
        self.assertIn("SET statement_timeout = '300s';\nSET idle_in_transaction_session_timeout = '600s';\nSET search_path TO app;\nSET application_name = 'b';\nselect current_database()", starts[0])
        c.close()

    def test_web_ui_state_save_and_test(self):
        state = self.http("/api/state")
        self.assertEqual(state["config"]["password"], web.MASK)
        self.assertIn("test/main", state["servers"])
        cfg = dict(state["config"], timeout=5)
        saved = self.http("/api/config", cfg)
        self.assertEqual(saved["config"]["timeout"], 5)
        self.assertEqual(config.load()["password"], "pw")
        self.assertEqual(config.load()["instances"][0]["password"], "secret")
        r = self.http("/api/test", dict(cfg["instances"][0], password="wrong"))
        self.assertIn("login rejected", r["error"])
        self.assertEqual(self.http("/api/test", cfg["instances"][0])["servers"], ["main", "other"])
        self.assertIn("<title>pgproxy</title>", self.http("/"))

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

    @unittest.skipUnless(shutil.which("psql"), "psql not installed")
    def test_psql(self):
        r = subprocess.run(["psql", f"host=127.0.0.1 port={self.port} user=main dbname=app", "-X", "-At",
                            "-c", "select 1", "-c", "select * from nope"],
                           capture_output=True, text=True, env=dict(os.environ, PGPASSWORD="pw"), timeout=30)
        self.assertEqual(r.stdout, "1|t|\n2|f|x\n")
        self.assertIn('relation "nope" does not exist', r.stderr)


if __name__ == "__main__":
    unittest.main()
