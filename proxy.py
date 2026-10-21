"""PostgreSQL wire server: every client connection becomes one pgAdmin Query Tool."""
import hmac
import json
import random
import re
import socketserver
import struct
import threading
import time

from log import log
from pgadmin import ApiError, Lost, PgAdmin, _items

NULL = b"\x00"
TX = {2: b"T", 3: b"E"}
SSL_REQUEST, GSS_REQUEST, CANCEL_REQUEST = 80877103, 80877104, 80877102
PARAMS = ("server_version", "server_encoding", "DateStyle", "TimeZone", "integer_datetimes", "standard_conforming_strings")


class Server(socketserver.ThreadingTCPServer):
    """Listens for PG clients; holds the config, the pgAdmin clients and the discovered server tree."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, cfg, cookie_dir):
        super().__init__(addr, Conn)
        self.cookie_dir, self.conns, self.nodes, self.scanned = cookie_dir, {}, {}, 0
        self.pgs, self.cfg = {}, {}
        self.reload(cfg)
        threading.Thread(target=self.reap, daemon=True).start()

    def reload(self, cfg):
        """Applies a new config: new pgAdmin clients for changed instances, tree rescanned on next use."""
        old, self.cfg = self.pgs, cfg
        self.pgs = {i["name"]: old.get(i["name"]) if _same(old.get(i["name"]), i) else PgAdmin(i, self.cookie_dir)
                    for i in cfg["instances"]}
        self.nodes, self.scanned = {}, 0

    def setup(self):
        """SET statements run at the start of every backend."""
        cfg, out = self.cfg, []
        if cfg["read_only"]:
            out.append("SET default_transaction_read_only = on")
        if cfg["timeout"]:
            out.append(f"SET statement_timeout = '{cfg['timeout']}s'")
        if cfg["idle"]:
            out.append(f"SET idle_in_transaction_session_timeout = '{cfg['idle']}s'")
        return out

    def locate(self, user):
        """Server node for a User of the form 'server' or 'instance/server'; rescans the trees on a miss."""
        key = user.lower()
        node = self.nodes.get(key)
        if not node and time.monotonic() - self.scanned > 60:
            node = self.scan().get(key)
        if node == "ambiguous":
            raise ApiError(f"'{user}' exists in several pgAdmin instances; use User = instance/server")
        if not node:
            raise ApiError(f"no pgAdmin server '{user}'; use one of: {self.names()}")
        return self.pgs[node["inst"]], node

    def scan(self):
        self.scanned, self.nodes = time.monotonic(), {}
        for name, pg in self.pgs.items():
            try:
                for s in pg.servers():
                    node = dict(s, inst=name)
                    self.nodes[f"{name}/{s['label']}".lower()] = node
                    self.nodes[s["label"].lower()] = "ambiguous" if s["label"].lower() in self.nodes else node
            except ApiError as e:
                log(f"[!] pgAdmin {name}: {e}")
        return self.nodes

    def names(self):
        return ", ".join(sorted(k for k, n in self.nodes.items() if n != "ambiguous" and "/" in k))

    def status(self):
        """Live view for the web UI."""
        now = time.monotonic()
        return {
            "servers": sorted({f"{n['inst']}/{n['label']}" for n in self.nodes.values() if n != "ambiguous"}),
            "connections": [{"client": c.client_address[0], "target": c.name, "backend": bool(c.tid),
                             "in_tx": c.tx != b"I", "idle_s": int(now - c.last), "key": f"{k[0]}:{k[1]}"}
                            for k, c in list(self.conns.items())],
        }

    def reap(self):
        """Frees backends of connections silent for longer than idle and not inside a transaction."""
        while True:
            time.sleep(30)
            idle = self.cfg["idle"]
            for conn in list(self.conns.values()):
                with conn.lock:
                    if idle and conn.tid and conn.tx == b"I" and time.monotonic() - conn.last > idle:
                        conn.detach(f"idle {idle // 60} min")


class Conn(socketserver.BaseRequestHandler):
    """One client connection = one Query Tool = one Postgres backend (while it is not idle)."""

    def handle(self):
        self.w, self.pg, self.tid, self.tx, self.name = Wire(self.request), None, None, b"I", "?"
        self.sets, self.last, self.lock = [], time.monotonic(), threading.Lock()
        key = (random.randint(1, 2**31 - 1), random.randint(1, 2**31 - 1))
        try:
            kind, params = self.w.startup()
            if kind == "cancel":
                return self.cancel(params)
            if not self.server.cfg["password"]:
                return self.w.error(_error("proxy password is not set yet: open the web UI", "28000", "FATAL"))
            if not hmac.compare_digest(self.w.password().encode(), self.server.cfg["password"].encode()):
                return self.w.error(_error("wrong password (the proxy password from the web UI)", "28P01", "FATAL"))
            self.w.banner(self.open(params), key)
            self.server.conns[key] = self
            self.loop()
        except ApiError as e:
            log(f"[!] {self.name}: {e}")
            self.w.error(_error(str(e), "08006", "FATAL"))
        except OSError:
            pass
        finally:
            self.server.conns.pop(key, None)
            self.close()

    def open(self, params):
        """User = pgAdmin server, Database = database on it; -> ParameterStatus values for the client."""
        user, db = params.get("user", ""), params.get("database", "")
        self.name = f"{user}/{db}"
        self.pg, self.node = self.server.locate(user)
        self.db = db or self.node.get("db")
        values = self.attach()["result"][0]
        if values[0] != self.db:
            raise ApiError(f"pgAdmin opened database '{values[0]}' instead of '{self.db}'")
        log(f"[+] {self.name} (tid {self.tid})")
        return dict(zip(PARAMS, map(str, values[1:])), client_encoding="UTF8")

    def attach(self):
        """Opens a Query Tool (= backend) and replays the session SETs in one statement; -> pgAdmin answer."""
        node, gid, sid = self.node, self.node["gid"], self.node["_id"]
        if not node.get("connected"):
            self.pg.call("POST", f"/browser/server/connect/{gid}/{sid}", {})
            node["connected"] = True
        self.tid = random.randint(1_000_000, 9_999_999)
        try:
            self.pg.call("POST", f"/sqleditor/initialize/sqleditor/{self.tid}/{gid}/{sid}", {"dbname": self.db})
        except ApiError:
            node["connected"], self.tid = False, None
            raise
        self.pg.call("POST", f"/sqleditor/auto_commit/{self.tid}", True)
        self.pg.call("POST", f"/sqleditor/auto_rollback/{self.tid}", False)
        probe = "select current_database(), " + ", ".join(f"current_setting('{p}')" for p in PARAMS)
        return self.run(";\n".join(self.server.setup() + self.sets + [probe]))

    def detach(self, why):
        """Closes the Query Tool; the next query opens a new one and replays the SETs."""
        tid, self.tid = self.tid, None
        log(f"[~] {self.name}: backend released ({why})")
        try:
            self.pg.call("DELETE", f"/sqleditor/close/{tid}")
        except (ApiError, OSError):
            pass

    def loop(self):
        skip = False
        while True:
            kind, body = self.w.message()
            if kind == b"X":
                return
            if kind == b"Q":
                with self.lock:
                    self.query(body.rstrip(NULL).decode("utf-8", "replace"))
                self.w.ready(self.tx)
            elif kind == b"S":
                skip = False
                self.w.ready(self.tx)
            elif not skip:
                skip = True
                self.w.error(_error("extended query protocol is not supported: set preferQueryMode=simple", "0A000"))

    def query(self, sql):
        if not sql.strip():
            return self.w.send(b"I")
        start = time.monotonic()
        try:
            d = self.run(sql)
            if d.get("additional_messages"):
                self.w.error(_error(d["additional_messages"], "00000", "NOTICE"), b"N")
            if d.get("colinfo") is not None:
                self.w.columns(d["colinfo"])
            n = self.stream(d)
        except Lost:
            raise
        except ApiError as e:
            err = _error(str(e))
            log(f"{self.name} {_ms(start)} ERROR {err['C']} | {_oneline(sql)}")
            return self.w.error(err)
        status = d["result"] if isinstance(d.get("result"), str) else ""
        tag = (status.strip().splitlines() or [f"SELECT {n}"])[-1]
        log(f"{self.name} {_ms(start)} {tag} | {_oneline(sql)}")
        self.w.send(b"C", tag.encode() + NULL)
        if tag == "SET" and self.tx == b"I" and not re.match(r"\s*set\s+(local|transaction|session\s+char)", sql, re.I):
            self.sets = [s for s in self.sets if _setvar(s) != _setvar(sql)] + [sql]

    def run(self, sql):
        """Runs SQL in the Query Tool (opening one if released) and waits; -> data of pgAdmin's poll."""
        if self.tid is None:
            self.attach()
        self.last = time.monotonic()
        started = self.pg.call("POST", f"/sqleditor/query_tool/start/{self.tid}", {"sql": sql, "explain_plan": None})
        if (started.get("data") or {}).get("status") is False:
            raise Lost(f"pgAdmin did not start the query: {started['data'].get('result')}")
        delay = 0
        while True:
            time.sleep(delay)
            try:
                d = self.pg.call("GET", f"/sqleditor/poll/{self.tid}")["data"]
            except ApiError as e:
                self.tx = TX.get(e.tx, b"I")
                raise
            if d.get("status") != "Busy":
                break
            delay = min(delay * 2 or 0.02, 2)
        self.tx = TX.get(d.get("transaction_status"), b"I")
        if d.get("status") == "Cancel":
            raise ApiError("ERROR:  canceling statement due to user request\nSQL state: 57014")
        if d.get("status") != "Success":
            raise Lost(f"pgAdmin: {d.get('status')}: {d.get('result')}")
        return d

    def stream(self, d):
        """Sends rows to the client batch by batch as pgAdmin hands them out; -> row count."""
        n, batch, more = 0, _rows(d.get("result")), d.get("has_more_rows")
        while True:
            self.w.rows(batch)
            n += len(batch)
            if not more:
                return n
            f = self.pg.call("GET", f"/sqleditor/fetch/{self.tid}")["data"]
            batch = _rows(f.get("result"))
            more = bool(batch) and f.get("has_more_rows")

    def cancel(self, key):
        conn = self.server.conns.get(tuple(key))
        if conn and conn.tid:
            log(f"[x] {conn.name}: cancel requested")
            try:
                conn.pg.call("POST", f"/sqleditor/cancel/{conn.tid}")
            except (ApiError, OSError):
                pass

    def close(self):
        if self.tid:
            self.detach("client disconnected")


class Wire:
    """Server side of the PostgreSQL wire protocol v3 on a socket."""

    def __init__(self, sock):
        self.sock, self.buf = sock, b""

    def recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("client closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def message(self):
        kind = self.recv(1)
        return kind, self.recv(struct.unpack("!i", self.recv(4))[0] - 4)

    def send(self, kind, body=b""):
        self.sock.sendall(kind + struct.pack("!i", len(body) + 4) + body)

    def startup(self):
        """-> ("start", params) or ("cancel", (pid, key))."""
        while True:
            payload = self.recv(struct.unpack("!i", self.recv(4))[0] - 4)
            code = struct.unpack("!i", payload[:4])[0]
            if code in (SSL_REQUEST, GSS_REQUEST):
                self.sock.sendall(b"N")
            elif code == CANCEL_REQUEST:
                return "cancel", struct.unpack("!ii", payload[4:12])
            else:
                parts = payload[4:].split(NULL)
                return "start", {k.decode(): v.decode() for k, v in zip(parts[0::2], parts[1::2]) if k}

    def password(self):
        self.send(b"R", struct.pack("!i", 3))
        kind, body = self.message()
        if kind != b"p":
            raise ConnectionError("expected a password message")
        return body.rstrip(NULL).decode("utf-8", "replace")

    def banner(self, params, key):
        self.send(b"R", struct.pack("!i", 0))
        for k, v in params.items():
            self.send(b"S", k.encode() + NULL + v.encode() + NULL)
        self.send(b"K", struct.pack("!ii", *key))
        self.ready(b"I")

    def columns(self, colinfo):
        body = struct.pack("!h", len(colinfo))
        for c in colinfo:
            name = c.get("display_name") or c["name"]
            body += name.encode() + NULL + struct.pack(
                "!IhIhih", c.get("table_oid") or 0, c.get("table_column") or 0, c.get("type_code") or 25, -1, -1, 0)
        self.send(b"T", body)

    def rows(self, batch):
        out = []
        for values in batch:
            body = struct.pack("!h", len(values))
            for v in values:
                b = _text(v)
                body += struct.pack("!i", -1) if b is None else struct.pack("!i", len(b)) + b
            out.append(b"D" + struct.pack("!i", len(body) + 4) + body)
        self.sock.sendall(b"".join(out))

    def error(self, fields, kind=b"E"):
        self.send(kind, b"".join(k.encode() + str(v).encode() + NULL for k, v in fields.items() if v) + NULL)

    def ready(self, tx):
        self.send(b"Z", tx)


def _same(pg, inst):
    return pg and (pg.base, pg.email, pg.password) == (inst["url"].rstrip("/"), inst["email"], inst["password"])


def _rows(result):
    return result if isinstance(result, list) else []


def _text(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return b"t" if v else b"f"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str).encode()
    return str(v).encode()


def _error(msg, code=None, severity="ERROR"):
    """pgAdmin error text ("ERROR:  …\\nSQL state: 42P01\\nCharacter: 15") -> ErrorResponse fields."""
    return {
        "S": severity,
        "V": severity,
        "C": code or _field(msg, r"^SQL state: (\w{5})") or "XX000",
        "M": _field(msg, rf"^{severity}:\s+(.+)") or msg.strip(),
        "D": _field(msg, r"^Detail: (.+)"),
        "H": _field(msg, r"^Hint: (.+)"),
        "P": _field(msg, r"^Character: (\d+)"),
    }


def _field(text, pattern):
    m = re.search(pattern, text, re.M)
    return m and m.group(1).strip()


def _setvar(sql):
    return (sql.split() + [""])[1].lower()


def _ms(start):
    return f"{(time.monotonic() - start) * 1000:6.0f}ms"


def _oneline(sql, limit=500):
    s = " ".join(sql.split())
    return s if len(s) <= limit else s[:limit] + f"… ({len(s)} chars)"
