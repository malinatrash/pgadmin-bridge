"""One pgAdmin Query Tool = one Postgres backend: open, run SQL and poll, fetch rows, cancel, close."""
import random
import threading
import time

from .client import ApiError, Lost

# pgAdmin's transaction_status -> ReadyForQuery status byte (anything else is idle)
TX = {2: b"T", 3: b"E"}
POLL_MIN, POLL_MAX = 0.02, 0.5


class QueryTool:
    """A Query Tool on one server and database, bound to one lane of its pgAdmin; tid is None while no backend is open."""

    def __init__(self, pg, node, db):
        self.pg, self.node, self.db = pg, node, db
        self.lane, self.tid, self.tx = None, None, b"I"
        self.expect = 0.0  # moving average of how long this tool's statements take, drives the poll schedule

    def open(self):
        """Connects the server in pgAdmin (once per lane) and initializes a Query Tool."""
        gid, sid = self.node["gid"], self.node["_id"]
        self.lane = ln = self.pg.lane(reserve=True)
        self.tid = random.randint(1_000_000, 9_999_999)
        try:
            if sid not in ln.connected:
                ln.call("POST", f"/browser/server/connect/{gid}/{sid}", {})
                ln.connected.add(sid)
            ln.call("POST", f"/sqleditor/initialize/sqleditor/{self.tid}/{gid}/{sid}", {"dbname": self.db})
        except ApiError:
            ln.connected.discard(sid)
            self.tid = None
            self.pg.release(ln)
            raise
        if self.pg.autocommit_calls:
            self.autocommit()

    def autocommit(self):
        """pgAdmin's defaults are autocommit on / auto-rollback off; only called when a backend proved otherwise."""
        self.lane.call("POST", f"/sqleditor/auto_commit/{self.tid}", True)
        self.lane.call("POST", f"/sqleditor/auto_rollback/{self.tid}", False)

    def forget(self):
        """Drops a backend that pgAdmin has already lost; -> its tid."""
        tid, self.tid = self.tid, None
        if tid is not None:
            self.pg.release(self.lane)
        return tid

    def close(self):
        tid = self.forget()
        if tid is None:
            return
        try:
            self.lane.call("DELETE", f"/sqleditor/close/{tid}")
        except (ApiError, OSError):
            pass

    def run(self, sql):
        """Starts SQL and polls until it is done; -> data of pgAdmin's poll answer."""
        start = time.monotonic()
        started = self.lane.call("POST", f"/sqleditor/query_tool/start/{self.tid}", {"sql": sql, "explain_plan": None}, safe=True)
        if (started.get("data") or {}).get("status") is False:
            raise Lost(f"pgAdmin did not start the query: {started['data'].get('result')}", safe=True)
        delay = 0
        while True:
            time.sleep(delay)
            try:
                d = self.lane.call("GET", f"/sqleditor/poll/{self.tid}")["data"]
            except ApiError as e:
                self.tx = TX.get(e.tx, b"I")
                raise
            if d.get("status") != "Busy":
                break
            # first poll right away (the start round trip usually covers a short statement), then grow from a guess
            # based on this tool's history towards POLL_MAX; never sleep for longer than it took so far
            delay = min(max(delay * 2, POLL_MIN, self.expect / 4), POLL_MAX, time.monotonic() - start)
        took = time.monotonic() - start
        self.expect = took if not self.expect else self.expect * 0.7 + took * 0.3
        self.tx = TX.get(d.get("transaction_status"), b"I")
        if d.get("status") == "Cancel":
            raise ApiError("ERROR:  canceling statement due to user request\nSQL state: 57014")
        if d.get("status") != "Success":
            raise Lost(f"pgAdmin: {d.get('status')}: {d.get('result')}")
        return d

    def fetch(self, d):
        """Yields the rows of a finished query chunk by chunk; the next chunk is requested while the current one streams."""
        batch, more = _rows(d.get("result")), d.get("has_more_rows")
        while True:
            pending = _Prefetch(self._fetch) if more else None
            yield from batch
            if pending is None:
                return
            f = pending.result()
            batch = _rows(f.get("result"))
            more = bool(batch) and f.get("has_more_rows")

    def _fetch(self):
        """One fetch call: everything that is left in one go where pgAdmin supports it, a page of rows otherwise."""
        pg = self.pg
        if pg.fetch_all is not False:
            try:
                f = self.lane.call("GET", f"/sqleditor/fetch/{self.tid}/1")["data"]
                pg.fetch_all = True
                return dict(f, has_more_rows=False)
            except ApiError as e:
                if isinstance(e, Lost) or pg.fetch_all:
                    raise
                pg.fetch_all = False  # this pgAdmin has no fetch_all route
        return self.lane.call("GET", f"/sqleditor/fetch/{self.tid}")["data"]

    def cancel(self):
        try:
            self.lane.call("POST", f"/sqleditor/cancel/{self.tid}")
        except (ApiError, OSError):
            pass


class _Prefetch:
    """Runs fn in a thread; result() re-raises what fn raised."""

    def __init__(self, fn):
        self.value, self.err = None, None
        self.t = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self.t.start()

    def _run(self, fn):
        try:
            self.value = fn()
        except BaseException as e:  # handed back to the caller in result()
            self.err = e

    def result(self):
        self.t.join()
        if self.err:
            raise self.err
        return self.value


def _rows(result):
    return result if isinstance(result, list) else []
