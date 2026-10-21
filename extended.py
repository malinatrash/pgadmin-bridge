"""Extended query protocol helpers: $n substitution into SQL text, binary parameter decoding, binary column encoding."""
import datetime
import re
import struct
import uuid

TYPES = {16: "bool", 17: "bytea", 18: "char", 19: "name", 20: "int8", 21: "int2", 23: "int4", 25: "text", 26: "oid",
         114: "json", 700: "float4", 701: "float8", 1042: "bpchar", 1043: "varchar", 1082: "date", 1083: "time",
         1114: "timestamp", 1184: "timestamptz", 1186: "interval", 1700: "numeric", 2950: "uuid", 3802: "jsonb"}
TEXT_ONLY = {600, 603, 1005, 1007, 1016, 1021, 1022, 1266}
BINARY = {16, 17, 20, 21, 23, 26, 700, 701, 1082, 1083, 1114, 1184, 1700, 2950}
EPOCH = datetime.datetime(2000, 1, 1)
EPOCH_TZ = EPOCH.replace(tzinfo=datetime.timezone.utc)
DATE0 = datetime.date(2000, 1, 1)
TOKEN = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|--[^\n]*|/\*.*?\*/|(\$[A-Za-z_]*\$).*?\1|\$(\d+)""", re.S)


class ProtocolError(Exception):
    pass


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
    cast = f"::{TYPES[oid]}" if oid in TYPES else ""
    if raw is None:
        return "NULL" + cast
    text = decode(raw, oid) if fmt == 1 else raw.decode("utf-8", "replace")
    if oid == 17:
        return f"'\\x{raw.hex()}'::bytea" if fmt == 1 else f"'{text.replace(chr(39), chr(39) * 2)}'::bytea"
    return "'" + text.replace("'", "''") + "'" + cast


def decode(raw, oid):
    """Binary parameter -> its text form."""
    if oid == 16:
        return "true" if raw != b"\x00" else "false"
    if oid in (21, 23, 20, 26):
        return str(struct.unpack({2: "!h", 4: "!i", 8: "!q"}[len(raw)], raw)[0])
    if oid in (700, 701):
        return repr(struct.unpack("!f" if len(raw) == 4 else "!d", raw)[0])
    if oid == 2950:
        return str(uuid.UUID(bytes=raw))
    if oid == 1082:
        return (DATE0 + datetime.timedelta(days=struct.unpack("!i", raw)[0])).isoformat()
    if oid == 1083:
        return str(EPOCH + datetime.timedelta(microseconds=struct.unpack("!q", raw)[0]))[11:]
    if oid in (1114, 1184):
        us = struct.unpack("!q", raw)[0]
        base = EPOCH_TZ if oid == 1184 else EPOCH
        return (base + datetime.timedelta(microseconds=us)).isoformat(" ")
    raise ProtocolError(f"binary parameter of type {TYPES.get(oid, oid)} is not supported")


def column_oid(oid):
    """Types we cannot ship in binary are advertised as text so the driver never asks for binary."""
    return 25 if oid in TEXT_ONLY else oid or 25


def encode(value, oid):
    """Column value (as pgAdmin returns it) -> binary wire form; None when text is the only option."""
    s = str(value)
    if oid == 16:
        return b"\x01" if value in (True, "true", "t") else b"\x00"
    if oid == 21:
        return struct.pack("!h", int(s))
    if oid in (23, 26):
        return struct.pack("!i", int(s))
    if oid == 20:
        return struct.pack("!q", int(s))
    if oid == 700:
        return struct.pack("!f", float(s))
    if oid == 701:
        return struct.pack("!d", float(s))
    if oid == 2950:
        return uuid.UUID(s).bytes
    if oid == 17:
        return bytes.fromhex(s[2:]) if s.startswith("\\x") else s.encode("latin-1")
    if oid == 1082:
        return struct.pack("!i", _special(s, 2**31) if s in ("infinity", "-infinity") else (datetime.date.fromisoformat(s) - DATE0).days)
    if oid == 1083:
        t = datetime.time.fromisoformat(s)
        return struct.pack("!q", ((t.hour * 60 + t.minute) * 60 + t.second) * 1_000_000 + t.microsecond)
    if oid in (1114, 1184):
        if s in ("infinity", "-infinity"):
            return struct.pack("!q", _special(s, 2**63))
        dt = datetime.datetime.fromisoformat(_isofix(s))
        delta = dt - (EPOCH_TZ if dt.tzinfo else EPOCH)
        return struct.pack("!q", (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds)
    if oid == 1700:
        return numeric(s)
    return None


def numeric(s):
    """Text numeric -> PostgreSQL binary numeric (base-10000 digits)."""
    if s.lower() in ("nan", "infinity", "-infinity"):
        return struct.pack("!hhhh", 0, 0, {"nan": 0xC000, "infinity": 0xD000, "-infinity": 0xF000}[s.lower()], 0)
    sign = 0x4000 if s.startswith("-") else 0
    s = s.lstrip("+-")
    ip, _, fp = s.partition(".")
    ip = ip.lstrip("0") or ""
    ip = "0" * (-len(ip) % 4) + ip
    fp_padded = fp + "0" * (-len(fp) % 4)
    digits = [int(ip[i:i + 4]) for i in range(0, len(ip), 4)] + [int(fp_padded[i:i + 4]) for i in range(0, len(fp_padded), 4)]
    weight = len(ip) // 4 - 1
    while digits and digits[0] == 0:
        digits.pop(0)
        weight -= 1
    while digits and digits[-1] == 0:
        digits.pop()
    if not digits:
        weight = 0
    return struct.pack("!hhhh", len(digits), weight, sign, len(fp)) + b"".join(struct.pack("!h", d) for d in digits)


def _special(s, limit):
    return limit - 1 if s == "infinity" else -limit


def _isofix(s):
    m = re.search(r"([+-]\d{2})$", s)
    return s + ":00" if m else s
