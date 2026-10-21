"""PostgreSQL wire server: every client connection becomes one pgAdmin Query Tool."""
import hmac
import json
import random
import re
import socketserver
import struct
import threading
import time

from extended import BINARY, ProtocolError, column_oid, encode, substitute
from log import log
from pgadmin import ApiError, Lost, PgAdmin

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
        """Simple protocol (Q) streams rows; extended (P/B/D/E/C/H/S) runs at Describe/Execute and buffers them."""
        self.stmts, self.portals, failed = {}, {}, False
        while True:
            kind, body = self.w.message()
            if kind == b"X":
                return
            if kind == b"S":
                failed = False
                self.w.ready(self.tx)
            elif kind == b"Q":
                with self.lock:
                    self.query(body.rstrip(NULL).decode("utf-8", "replace"))
                self.w.ready(self.tx)
            elif failed or kind == b"H":
                continue
            else:
                try:
                    with self.lock:
                        self.extended(kind, body)
                except Lost:
                    raise
                except (ApiError, ProtocolError, ValueError, KeyError, struct.error) as e:
                    failed = True
                    self.w.error(_error(str(e), None if isinstance(e, ApiError) else "08P01"))

    def query(self, sql):
        if not sql.strip():
            return self.w.send(b"I")
        try:
            d = self.execute(sql)
            fmts = [0] * len(d["colinfo"] or [])
            if d["colinfo"] is not None:
                self.w.columns(d["colinfo"], fmts)
            self.w.rows(d["rows"], d["colinfo"], fmts)
        except Lost:
            raise
        except ApiError as e:
            return self.w.error(_error(str(e)))
        self.w.send(b"C", d["tag"].encode() + NULL)

    def extended(self, kind, body):
        w = self.w
        if kind == b"P":
            name, _, rest = body.partition(NULL)
            sql, _, rest = rest.partition(NULL)
            n = struct.unpack("!h", rest[:2])[0]
            self.stmts[name.decode()] = (sql.decode("utf-8", "replace"), list(struct.unpack(f"!{n}I", rest[2:2 + 4 * n])))
            w.send(b"1")
        elif kind == b"B":
            portal, _, rest = body.partition(NULL)
            stmt, _, rest = rest.partition(NULL)
            sql, oids = self.stmts[stmt.decode()]
            params, rfmts = _bind(rest)
            self.portals[portal.decode()] = {"sql": substitute(sql, params, oids), "rfmts": rfmts, "result": None, "sent": 0}
            w.send(b"2")
        elif kind == b"D":
            name = body[1:].partition(NULL)[0].decode()
            if body[:1] == b"S":
                oids = self.stmts[name][1]
                w.send(b"t", struct.pack("!h", len(oids)) + b"".join(struct.pack("!I", o or 25) for o in oids))
                w.send(b"n")
            else:
                d = self.portal(name)
                w.send(b"n") if d["colinfo"] is None else w.columns(d["colinfo"], d["fmts"])
        elif kind == b"E":
            name, _, rest = body.partition(NULL)
            name, maxrows = name.decode(), struct.unpack("!i", rest[:4])[0]
            d = self.portal(name)
            if d["tag"] is None:
                return w.send(b"I")
            p = self.portals[name]
            chunk = d["rows"][p["sent"]:p["sent"] + maxrows] if maxrows > 0 else d["rows"][p["sent"]:]
            w.rows(chunk, d["colinfo"], d["fmts"])
            p["sent"] += len(chunk)
            w.send(b"s") if p["sent"] < len(d["rows"]) else w.send(b"C", d["tag"].encode() + NULL)
        elif kind == b"C":
            (self.stmts if body[:1] == b"S" else self.portals).pop(body[1:].partition(NULL)[0].decode(), None)
            w.send(b"3")
        else:
            raise ProtocolError(f"unsupported message type {kind!r}")

    def portal(self, name):
        """Runs the portal's SQL once; -> result with the column formats the client asked for."""
        p = self.portals[name]
        if p["result"] is None:
            if not p["sql"].strip():
                p["result"] = {"colinfo": None, "rows": [], "tag": None, "fmts": []}
            else:
                d, rf = self.execute(p["sql"]), p["rfmts"]
                d["fmts"] = []
                for i, c in enumerate(d["colinfo"] or []):
                    want = rf[i] if len(rf) > 1 else (rf[0] if rf else 0)
                    d["fmts"].append(1 if want and column_oid(c.get("type_code")) in BINARY else 0)
                p["result"] = d
        return p["result"]

    def execute(self, sql):
        """Runs SQL, forwards NOTICEs, collects all rows; -> {colinfo, rows, tag}. Logs the outcome."""
        start = time.monotonic()
        try:
            d = self.run(sql)
        except ApiError as e:
            log(f"{self.name} {_ms(start)} ERROR {_error(str(e))['C']} | {_oneline(sql)}")
            raise
        if d.get("additional_messages"):
            self.w.error(_error(d["additional_messages"], "00000", "NOTICE"), b"N")
        rows = list(self.fetch(d))
        status = d["result"] if isinstance(d.get("result"), str) else ""
        tag = (status.strip().splitlines() or [f"SELECT {len(rows)}"])[-1]
        log(f"{self.name} {_ms(start)} {tag} | {_oneline(sql)}")
        if tag == "SET" and self.tx == b"I" and not re.match(r"\s*set\s+(local|transaction|session\s+char)", sql, re.I):
            self.sets = [s for s in self.sets if _setvar(s) != _setvar(sql)] + [sql]
        return {"colinfo": d.get("colinfo"), "rows": rows, "tag": tag}

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

    def fetch(self, d):
        """Yields rows as pgAdmin hands them out, chunk by chunk."""
        batch, more = _rows(d.get("result")), d.get("has_more_rows")
        while True:
            yield from batch
            if not more:
                return
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

    def columns(self, colinfo, fmts):
        body = struct.pack("!h", len(colinfo))
        for c, fmt in zip(colinfo, fmts):
            name = c.get("display_name") or c["name"]
            body += name.encode() + NULL + struct.pack(
                "!IhIhih", c.get("table_oid") or 0, c.get("table_column") or 0, column_oid(c.get("type_code")), -1, -1, fmt)
        self.send(b"T", body)

    def rows(self, batch, colinfo, fmts):
        oids = [column_oid(c.get("type_code")) for c in colinfo or []]
        out = []
        for values in batch:
            body = struct.pack("!h", len(values))
            for v, oid, fmt in zip(values, oids, fmts):
                b = None if v is None else encode(v, oid) if fmt else _text(v)
                body += struct.pack("!i", -1) if b is None else struct.pack("!i", len(b)) + b
            out.append(b"D" + struct.pack("!i", len(body) + 4) + body)
            if len(out) >= 500:
                self.sock.sendall(b"".join(out))
                out = []
        self.sock.sendall(b"".join(out))

    def error(self, fields, kind=b"E"):
        self.send(kind, b"".join(k.encode() + str(v).encode() + NULL for k, v in fields.items() if v) + NULL)

    def ready(self, tx):
        self.send(b"Z", tx)


def _same(pg, inst):
    return pg and (pg.base, pg.email, pg.password) == (inst["url"].rstrip("/"), inst["email"], inst["password"])


def _rows(result):
    return result if isinstance(result, list) else []


def _bind(raw):
    """Body of Bind after the two names -> ([(format, value|None)], result formats)."""
    n = struct.unpack("!h", raw[:2])[0]
    pfmts, pos = list(struct.unpack(f"!{n}h", raw[2:2 + 2 * n])), 2 + 2 * n
    count = struct.unpack("!h", raw[pos:pos + 2])[0]
    pos, params = pos + 2, []
    for i in range(count):
        size = struct.unpack("!i", raw[pos:pos + 4])[0]
        pos += 4
        value = None if size < 0 else raw[pos:pos + size]
        pos += max(size, 0)
        params.append(((pfmts[i] if n > 1 else pfmts[0]) if pfmts else 0, value))
    m = struct.unpack("!h", raw[pos:pos + 2])[0]
    return params, list(struct.unpack(f"!{m}h", raw[pos + 2:pos + 2 + 2 * m]))


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
