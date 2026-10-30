"""Extended query protocol: Parse/Bind/Describe/Execute/Close on top of the session's execute()."""
import struct

from ..pg.errors import ProtocolError
from ..pg.params import parse_bind, substitute
from ..pg.types import BINARY, column_oid
from ..pg.wire import NULL


class Extended:
    """Mixed into Session: statements and portals live in self.stmts / self.portals; a portal runs its SQL once."""

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
            params, rfmts = parse_bind(rest)
            self.portals[portal.decode()] = {"sql": substitute(sql, params, oids), "rfmts": rfmts, "result": None}
            w.send(b"2")
        elif kind == b"D":
            name = body[1:].partition(NULL)[0].decode()
            if body[:1] == b"S":
                oids = self.stmts[name][1]
                w.send(b"t", struct.pack("!h", len(oids)) + b"".join(struct.pack("!I", o or 25) for o in oids))
                w.send(b"n")
            else:
                r = self.portal(name)
                w.send(b"n") if r.colinfo is None else w.columns(r.colinfo, r.fmts)
        elif kind == b"E":
            name, _, rest = body.partition(NULL)
            name, maxrows = name.decode(), struct.unpack("!i", rest[:4])[0]
            r = self.portal(name)
            if r is EMPTY:
                return w.send(b"I")
            if maxrows > 0 or r.buf is not None:
                # a row limit means the portal may be suspended and resumed: its rows have to be kept around
                rows = r.all()
                chunk = rows[r.sent:r.sent + maxrows] if maxrows > 0 else rows[r.sent:]
                w.rows(chunk, r.colinfo, r.fmts)
                r.sent += len(chunk)
                return w.send(b"s") if r.sent < len(rows) else w.complete(r.tag)
            w.rows(r.stream(), r.colinfo, r.fmts)
            w.complete(r.tag)
        elif kind == b"C":
            (self.stmts if body[:1] == b"S" else self.portals).pop(body[1:].partition(NULL)[0].decode(), None)
            w.send(b"3")
        else:
            raise ProtocolError(f"unsupported message type {kind!r}")

    def portal(self, name):
        """Runs the portal's SQL once; -> Result with the column formats the client asked for (EMPTY for a blank query)."""
        p = self.portals[name]
        if p["result"] is None:
            if not p["sql"].strip():
                p["result"] = EMPTY
            else:
                r, rf = self.execute(p["sql"]), p["rfmts"]
                for i, c in enumerate(r.colinfo or []):
                    want = rf[i] if len(rf) > 1 else (rf[0] if rf else 0)
                    r.fmts[i] = 1 if want and column_oid(c.get("type_code")) in BINARY else 0
                p["result"] = r
        return p["result"]


class _Empty:
    colinfo = None


EMPTY = _Empty()
