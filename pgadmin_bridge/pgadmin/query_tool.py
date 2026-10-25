"""One pgAdmin Query Tool = one Postgres backend: open, run SQL and poll, fetch rows, cancel, close."""
import random
import time

from .client import ApiError, Lost

# pgAdmin's transaction_status -> ReadyForQuery status byte (anything else is idle)
TX = {2: b"T", 3: b"E"}


class QueryTool:
    """A Query Tool on one server and database; tid is None while no backend is open."""

    def __init__(self, pg, node, db):
        self.pg, self.node, self.db = pg, node, db
        self.tid, self.tx = None, b"I"

    def open(self):
        """Connects the server in pgAdmin if needed and initializes a Query Tool in autocommit mode."""
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

    def close(self):
        tid, self.tid = self.tid, None
        try:
            self.pg.call("DELETE", f"/sqleditor/close/{tid}")
        except (ApiError, OSError):
            pass

    def run(self, sql):
        """Starts SQL and polls until it is done; -> data of pgAdmin's poll answer."""
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
        """Yields the rows of a finished query as pgAdmin hands them out, chunk by chunk."""
        batch, more = _rows(d.get("result")), d.get("has_more_rows")
        while True:
            yield from batch
            if not more:
                return
            f = self.pg.call("GET", f"/sqleditor/fetch/{self.tid}")["data"]
            batch = _rows(f.get("result"))
            more = bool(batch) and f.get("has_more_rows")

    def cancel(self):
        try:
            self.pg.call("POST", f"/sqleditor/cancel/{self.tid}")
        except (ApiError, OSError):
            pass


def _rows(result):
    return result if isinstance(result, list) else []
