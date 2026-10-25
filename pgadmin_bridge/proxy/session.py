"""One client connection: authentication, the simple query protocol, SET tracking and idle release."""
import hmac
import random
import re
import socketserver
import struct
import threading
import time

from ..log import elapsed, log
from ..pg import errors
from ..pg.errors import ProtocolError
from ..pg.wire import NULL, Wire
from ..pgadmin import ApiError, Lost, QueryTool
from .extended import Extended

PARAMS = ("server_version", "server_encoding", "DateStyle", "TimeZone", "integer_datetimes", "standard_conforming_strings")


class Session(Extended, socketserver.BaseRequestHandler):
    """One client connection = one Query Tool = one Postgres backend (while it is not idle)."""

    def handle(self):
        self.w, self.tool, self.name, self.application = Wire(self.request), None, "?", ""
        self.sets, self.queries, self.lock = [], 0, threading.Lock()
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
        values = self.attach()["result"][0]
        if values[0] != self.tool.db:
            raise ApiError(f"pgAdmin opened database '{values[0]}' instead of '{self.tool.db}'")
        log(f"[+] {self.name} (tid {self.tool.tid})")
        return dict(zip(PARAMS, map(str, values[1:])), client_encoding="UTF8")

    def attach(self):
        """Opens a Query Tool (= backend) and replays the session SETs in one statement; -> pgAdmin answer."""
        self.tool.open()
        probe = "select current_database(), " + ", ".join(f"current_setting('{p}')" for p in PARAMS)
        return self.tool.run(";\n".join(self.server.setup() + self.sets + [probe]))

    def detach(self, why):
        """Closes the Query Tool; the next query opens a new one and replays the SETs."""
        log(f"[~] {self.name}: backend released ({why})")
        self.tool.close()

    def loop(self):
        """Simple protocol (Q) streams rows; extended (P/B/D/E/C/H/S) runs at Describe/Execute and buffers them."""
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
            return self.w.error(errors.fields(str(e)))
        self.w.complete(d["tag"])

    def execute(self, sql):
        """Runs SQL, forwards NOTICEs, collects all rows; -> {colinfo, rows, tag}. Logs the outcome."""
        start = time.monotonic()
        self.queries += 1
        try:
            d = self.run(sql)
        except ApiError as e:
            log(f"{self.name} {elapsed(start)} ERROR {errors.fields(str(e))['C']} | {_oneline(sql)}")
            raise
        if d.get("additional_messages"):
            self.w.error(errors.fields(d["additional_messages"], "00000", "NOTICE"), b"N")
        rows = list(self.tool.fetch(d))
        status = d["result"] if isinstance(d.get("result"), str) else ""
        tag = (status.strip().splitlines() or [f"SELECT {len(rows)}"])[-1]
        log(f"{self.name} {elapsed(start)} {tag} | {_oneline(sql)}")
        if tag == "SET" and self.tool.tx == b"I" and not re.match(r"\s*set\s+(local|transaction|session\s+char)", sql, re.I):
            self.sets = [s for s in self.sets if _setvar(s) != _setvar(sql)] + [sql]
        return {"colinfo": d.get("colinfo"), "rows": rows, "tag": tag}

    def run(self, sql):
        """Runs SQL in the Query Tool, opening a new one if the backend was released."""
        if self.tool.tid is None:
            self.attach()
        self.last = time.monotonic()
        return self.tool.run(sql)

    def cancel(self, key):
        conn = self.server.conns.get(tuple(key))
        if conn and conn.tool.tid:
            log(f"[x] {conn.name}: cancel requested")
            conn.tool.cancel()

    def close(self):
        if self.tool and self.tool.tid:
            self.detach("client disconnected")


def _setvar(sql):
    return (sql.split() + [""])[1].lower()


def _oneline(sql, limit=500):
    s = " ".join(sql.split())
    return s if len(s) <= limit else s[:limit] + f"… ({len(s)} chars)"
