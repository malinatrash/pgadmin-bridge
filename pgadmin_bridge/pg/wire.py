"""Message framing on a client socket: startup, authentication, row descriptions, data rows, errors."""
import json
import struct

from .types import column_oid, encode

NULL = b"\x00"
SSL_REQUEST, GSS_REQUEST, CANCEL_REQUEST = 80877103, 80877104, 80877102


class Wire:
    """Server side of the PostgreSQL wire protocol v3 on a socket."""

    def __init__(self, sock):
        self.sock, self.buf = sock, bytearray()

    def recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("client closed the connection")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
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
                return "start", {k.decode(): v.decode() for k, v in zip(parts[0::2], parts[1::2], strict=False) if k}

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
        for c, fmt in zip(colinfo, fmts, strict=False):
            name = c.get("display_name") or c["name"]
            body += name.encode() + NULL + struct.pack(
                "!IhIhih", c.get("table_oid") or 0, c.get("table_column") or 0, column_oid(c.get("type_code")), -1, -1, fmt)
        self.send(b"T", body)

    def rows(self, batch, colinfo, fmts):
        """Sends a DataRow per row of batch (any iterable, consumed as it goes), in writes of up to 500 rows."""
        oids = [column_oid(c.get("type_code")) for c in colinfo or []]
        pack_i, out, null = struct.Struct("!i").pack, [], struct.pack("!i", -1)
        for values in batch:
            parts = [struct.pack("!h", len(values))]
            for v, oid, fmt in zip(values, oids, fmts, strict=False):
                b = None if v is None else encode(v, oid) if fmt else text(v)
                parts.append(null if b is None else pack_i(len(b)) + b)
            body = b"".join(parts)
            out.append(b"D" + pack_i(len(body) + 4) + body)
            if len(out) >= 500:
                self.sock.sendall(b"".join(out))
                out = []
        if out:
            self.sock.sendall(b"".join(out))

    def complete(self, tag):
        self.send(b"C", tag.encode() + NULL)

    def error(self, fields, kind=b"E"):
        self.send(kind, b"".join(k.encode() + str(v).encode() + NULL for k, v in fields.items() if v) + NULL)

    def ready(self, tx):
        self.send(b"Z", tx)


def text(v):
    """Column value -> text wire form."""
    if v is None:
        return None
    if isinstance(v, bool):
        return b"t" if v else b"f"
    if isinstance(v, dict | list):
        return json.dumps(v, ensure_ascii=False, default=str).encode()
    return str(v).encode()
