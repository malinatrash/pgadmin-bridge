"""Bind parameters: parsing the Bind message and inlining $n as SQL literals (pgAdmin only takes SQL text)."""
import re
import struct

from .errors import ProtocolError
from .types import NAMES, decode

TOKEN = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|--[^\n]*|/\*.*?\*/|(\$[A-Za-z_]*\$).*?\1|\$(\d+)""", re.S)


def substitute(sql, params, oids):
    """Replaces $n with literals; params are (format, bytes|None), oids the declared types (0 = unknown)."""
    def repl(m):
        if not m.group(2):
            return m.group(0)
        n = int(m.group(2))
        if not 1 <= n <= len(params):
            raise ProtocolError(f"there is no parameter ${n}")
        oid = oids[n - 1] if n <= len(oids) else 0
        return literal(*params[n - 1], oid)
    return TOKEN.sub(repl, sql)


def literal(fmt, raw, oid):
    cast = f"::{NAMES[oid]}" if oid in NAMES else ""
    if raw is None:
        return "NULL" + cast
    text = decode(raw, oid) if fmt == 1 else raw.decode("utf-8", "replace")
    if oid == 17:
        return f"'\\x{raw.hex()}'::bytea" if fmt == 1 else f"'{text.replace(chr(39), chr(39) * 2)}'::bytea"
    return "'" + text.replace("'", "''") + "'" + cast


def parse_bind(raw):
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
