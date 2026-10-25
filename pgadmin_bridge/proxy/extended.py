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
            w.send(b"s") if p["sent"] < len(d["rows"]) else w.complete(d["tag"])
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
