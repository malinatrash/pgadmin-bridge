"""One client connection: authentication, the simple query protocol, SET tracking, local answers and idle release."""
import hmac
import random
import re
import socket
import socketserver
import struct
import threading
import time

from ..log import elapsed, log
from ..pg import errors
from ..pg.errors import ProtocolError
from ..pg.wire import NULL, Wire
from ..pgadmin import ApiError, Lost, QueryTool
from .cache import MAX_ROWS, cacheable
from .extended import Extended

PARAMS = ("server_version", "server_encoding", "DateStyle", "TimeZone", "integer_datetimes", "standard_conforming_strings")
# settings whose server-side value never reaches the client through this proxy: answered locally, not sent to pgAdmin
LOCAL_SETS = ("application_name", "client_encoding", "extra_float_digits")
PING = re.compile(r"^\s*select\s+1\s*;?\s*$", re.I)
VERSION = re.compile(r"^\s*select\s+version\s*\(\s*\)\s*;?\s*$", re.I)
SHOW = re.compile(r"^\s*show\s+(\w+)\s*;?\s*$", re.I)
LOCAL_SET = re.compile(r"^\s*set\s+(?:session\s+)?(" + "|".join(LOCAL_SETS) + r")\s*(?:=|\bto\b)\s*(.+?)\s*;?\s*$", re.I | re.S)
NOT_REPLAYED = re.compile(r"\s*set\s+(local|transaction|session\s+char)", re.I)
TEXT, INT4 = {"type_code": 25}, {"type_code": 23}


class Session(Extended, socketserver.BaseRequestHandler):
    """One client connection = one Query Tool = one Postgres backend (while it is not idle)."""

    def handle(self):
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.w, self.tool, self.name, self.application = Wire(self.request), None, "?", ""
        self.sets, self.queries, self.lock = [], 0, threading.Lock()
        self.params, self.version = {}, ""
        self.started = self.last = time.monotonic()
        key = (random.randint(1, 2**31 - 1), random.randint(1, 2**31 - 1))
        try:
            kind, params = self.w.startup()
            if kind == "cancel":
                return self.cancel(params)
            if not self.server.cfg["password"]:
                return self.w.error(errors.fields("proxy password is not set yet: open the web UI", "28000", "FATAL"))
            if not hmac.compare_digest(self.w.password().encode(), self.server.cfg["password"].encode()):
                return self.w.error(errors.fields("wrong password (the proxy password from the web UI)", "28P01", "FATAL"))
            self.w.banner(self.open(params), key)
            self.server.conns[key] = self
            self.loop()
        except ApiError as e:
            log(f"[!] {self.name}: {e}")
            self.w.error(errors.fields(str(e), "08006", "FATAL"))
        except OSError:
            pass
        finally:
            self.server.conns.pop(key, None)
            self.close()

    def open(self, params):
        """User = pgAdmin server, Database = database on it; -> ParameterStatus values for the client."""
        user, db = params.get("user", ""), params.get("database", "")
        self.name, self.application = f"{user}/{db}", params.get("application_name", "")
        pg, node = self.server.registry.locate(user)
        self.tool = QueryTool(pg, node, db or node.get("db"))
        self.attach()
        log(f"[+] {self.name} (tid {self.tool.tid} on {self.tool.lane})")
        return dict(self.params, client_encoding="UTF8")

    def attach(self):
        """Opens a Query Tool (= backend) and replays the session SETs in one statement together with a settings probe."""
        self.tool.open()
        probe = "select current_database(), version(), " + ", ".join(f"current_setting('{p}')" for p in PARAMS)
        d = self.tool.run(";\n".join(self.server.setup() + self.sets + [probe]))
        if self.tool.tx != b"I":
            # this pgAdmin user has autocommit off: switch it on for this and every later backend, then start over
            log(f"[~] {self.name}: pgAdmin autocommit is off, enabling it")
            self.tool.pg.autocommit_calls = True
            self.tool.autocommit()
            self.tool.run("ROLLBACK")
            d = self.tool.run(";\n".join(self.server.setup() + self.sets + [probe]))
        values = (d.get("result") or [[None]])[0]
        if values[0] != self.tool.db:
            raise ApiError(f"pgAdmin opened database '{values[0]}' instead of '{self.tool.db}'")
        self.version = str(values[1])
        self.params = dict(zip(PARAMS, map(str, values[2:]), strict=False))

    def detach(self, why):
        """Closes the Query Tool; the next query opens a new one and replays the SETs."""
        log(f"[~] {self.name}: backend released ({why})")
        self.tool.close()

    def loop(self):
        """Simple protocol (Q) streams rows; extended (P/B/D/E/C/H/S) runs at Describe/Execute."""
        self.stmts, self.portals, failed = {}, {}, False
        while True:
            kind, body = self.w.message()
            if kind == b"X":
                return
            if kind == b"S":
                failed = False
                self.w.ready(self.tool.tx)
            elif kind == b"Q":
                with self.lock:
                    self.query(body.rstrip(NULL).decode("utf-8", "replace"))
                self.w.ready(self.tool.tx)
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
                    self.w.error(errors.fields(str(e), None if isinstance(e, ApiError) else "08P01"))

    def query(self, sql):
        """Simple protocol: RowDescription, then DataRows as pgAdmin hands them out, then CommandComplete."""
        if not sql.strip():
            return self.w.send(b"I")
        try:
            r = self.execute(sql)
            if r.colinfo is not None:
                self.w.columns(r.colinfo, r.fmts)
            self.w.rows(r.stream(), r.colinfo, r.fmts)
        except Lost:
            raise
        except ApiError as e:
            return self.w.error(errors.fields(str(e)))
        self.w.complete(r.tag)

    def execute(self, sql):
        """Runs SQL (or answers it locally / from the catalog cache); -> Result whose rows are consumed once."""
        start = time.monotonic()
        self.queries += 1

        def finish(tag, how=""):
            log(f"{self.name} {elapsed(start)} {tag}{how} | {_oneline(sql)}")

        local = self.local(sql)
        if local is not None:
            colinfo, rows, status = local
            return Result(colinfo, rows, status, lambda tag: finish(tag, " (local)"))
        ttl = self.server.cfg.get("cache", 0)
        if ttl and self.tool.tx == b"I" and cacheable(sql):
            key = (self.tool.pg.name, self.tool.node["_id"], self.tool.db, tuple(self.sets), sql)
            colinfo, rows, status = self.server.cache.get(key, ttl, lambda: self.buffered(sql, start))
            return Result(colinfo, rows, status, lambda tag: finish(tag, " (cached)"))
        try:
            d = self.run(sql)
        except ApiError as e:
            log(f"{self.name} {elapsed(start)} ERROR {errors.fields(str(e))['C']} | {_oneline(sql)}")
            raise
        if d.get("additional_messages"):
            self.w.error(errors.fields(d["additional_messages"], "00000", "NOTICE"), b"N")
        status = d["result"] if isinstance(d.get("result"), str) else ""
        if status.strip().endswith("SET") and self.tool.tx == b"I" and not NOT_REPLAYED.match(sql):
            self.sets = [s for s in self.sets if _setvar(s) != _setvar(sql)] + [sql]
        return Result(d.get("colinfo"), self.tool.fetch(d), status, finish)

    def buffered(self, sql, start):
        """Runs SQL and collects every row (for the cache); -> ((colinfo, rows, status), keep)."""
        try:
            d = self.run(sql)
        except ApiError as e:
            log(f"{self.name} {elapsed(start)} ERROR {errors.fields(str(e))['C']} | {_oneline(sql)}")
            raise
        rows = list(self.tool.fetch(d))
        status = d["result"] if isinstance(d.get("result"), str) else ""
        return (d.get("colinfo"), rows, status), len(rows) <= MAX_ROWS

    def local(self, sql):
        """Statements answered without pgAdmin: keepalive pings, version(), SHOW of known settings, cosmetic SETs."""
        if self.tool.tx != b"I":
            return None
        if PING.match(sql):
            return [dict(INT4, name="?column?")], [[1]], ""
        if VERSION.match(sql):
            return [dict(TEXT, name="version")], [[self.version]], ""
        m = SHOW.match(sql)
        if m:
            name = m.group(1).lower()
            known = {p.lower(): v for p, v in self.params.items()} | {"client_encoding": "UTF8"}
            if name in known and all(_setvar(s) != name for s in self.sets):
                return [dict(TEXT, name=name)], [[known[name]]], ""
        m = LOCAL_SET.match(sql)
        if m:
            if m.group(1).lower() == "application_name":
                self.application = m.group(2).strip().strip("'\"")
            return None, [], "SET"
        return None

    def run(self, sql):
        """Runs SQL in the Query Tool, opening a new one if the backend was released or vanished before the statement ran."""
        if self.tool.tid is None:
            self.attach()
        self.last = time.monotonic()
        idle = self.tool.tx == b"I"
        try:
            return self.tool.run(sql)
        except Lost as e:
            if not (e.safe and idle):
                raise
            log(f"[~] {self.name}: backend vanished before the statement ran, re-attaching")
            self.tool.forget()
            self.attach()
            return self.tool.run(sql)

    def cancel(self, key):
        conn = self.server.conns.get(tuple(key))
        if conn and conn.tool.tid:
            log(f"[x] {conn.name}: cancel requested")
            conn.tool.cancel()

    def close(self):
        if self.tool and self.tool.tid:
            self.detach("client disconnected")


class Result:
    """The outcome of one statement. Rows are consumed once: streamed, or buffered when a portal is executed in pieces."""

    def __init__(self, colinfo, rows, status, finish):
        self.colinfo, self.status, self.finish = colinfo, status, finish
        self.rows, self.count, self.tag = iter(rows), 0, None
        self.fmts = [0] * len(colinfo or [])
        self.buf, self.sent = None, 0  # for suspended portals (Execute with a row limit)

    def stream(self):
        for row in self.rows:
            self.count += 1
            yield row
        self.end()

    def all(self):
        """Every remaining row, buffered."""
        if self.buf is None:
            self.buf = list(self.stream())
        return self.buf

    def end(self):
        if self.tag is None:
            self.tag = (self.status.strip().splitlines() or [f"SELECT {self.count}"])[-1]
            self.finish(self.tag)


def _setvar(sql):
    return (sql.split() + [""])[1].lower()


def _oneline(sql, limit=500):
    s = " ".join(sql.split())
    return s if len(s) <= limit else s[:limit] + f"… ({len(s)} chars)"
