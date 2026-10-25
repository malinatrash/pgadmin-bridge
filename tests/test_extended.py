"""Extended query protocol through the proxy: Parse/Bind/Describe/Execute/Sync, parameters, binary columns."""
import datetime
import os
import struct
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pgadmin_bridge.pg import types  # noqa: E402

from helpers import Base, pack  # noqa: E402


def parse(name, sql, oids=()):
    return pack(b"P", name.encode() + b"\0" + sql.encode() + b"\0" + struct.pack("!h", len(oids)) + b"".join(struct.pack("!I", o) for o in oids))


def bind(portal, stmt, params=(), pfmts=(), rfmts=()):
    body = portal.encode() + b"\0" + stmt.encode() + b"\0" + struct.pack("!h", len(pfmts)) + b"".join(struct.pack("!h", f) for f in pfmts)
    body += struct.pack("!h", len(params))
    for p in params:
        body += struct.pack("!i", -1) if p is None else struct.pack("!i", len(p)) + p
    return pack(b"B", body + struct.pack("!h", len(rfmts)) + b"".join(struct.pack("!h", f) for f in rfmts))


def describe(kind, name):
    return pack(b"D", kind.encode() + name.encode() + b"\0")


def execute(portal, maxrows=0):
    return pack(b"E", portal.encode() + b"\0" + struct.pack("!i", maxrows))


SYNC = pack(b"S", b"")


def cells(body):
    n, pos, out = struct.unpack("!h", body[:2])[0], 2, []
    for _ in range(n):
        size = struct.unpack("!i", body[pos:pos + 4])[0]
        pos += 4
        out.append(None if size < 0 else body[pos:pos + size])
        pos += max(size, 0)
    return out


class ExtendedTest(Base):
    def roundtrip(self, c, *msgs):
        c.s.sendall(b"".join(msgs))
        got = c.until_ready()
        return "".join(k.decode() for k, _ in got), got

    def test_parameters_are_substituted(self):
        c = self.connect()
        kinds, got = self.roundtrip(c, parse("", "echo $1, $2, $3, '$1', $$x $1$$ -- $2", (23, 0, 1043)),
                                    bind("", "", [b"5", b"it's", None]), describe("P", ""), execute(""), SYNC)
        self.assertEqual(kinds, "12TDCZ")
        self.assertEqual(cells(got[3][1])[0].decode(), "echo '5'::int4, 'it''s', NULL::varchar, '$1', $$x $1$$ -- $2")
        c.close()

    def test_binary_parameter_and_describe_statement(self):
        c = self.connect()
        kinds, got = self.roundtrip(c, parse("s1", "echo $1 $2 $3", (20, 16, 2950)), describe("S", "s1"),
                                    bind("p1", "s1", [struct.pack("!q", -2), b"\x01", uuid.UUID(int=7).bytes], [1, 1, 1]),
                                    execute("p1"), describe("P", "p1"), SYNC)
        self.assertEqual(kinds, "1tn2DCTZ")
        self.assertEqual(struct.unpack("!hIII", got[1][1]), (3, 20, 16, 2950))
        self.assertEqual(cells(got[4][1])[0].decode(), "echo '-2'::int8 'true'::bool '00000000-0000-0000-0000-000000000007'::uuid")
        c.close()

    def test_binary_columns(self):
        c = self.connect()
        kinds, got = self.roundtrip(c, parse("", "select typed"), bind("", "", rfmts=[1]), describe("P", ""), execute(""), SYNC)
        self.assertEqual(kinds, "12TDCZ")
        oids, fmts, pos, body = [], [], 2, got[2][1]
        for _ in range(struct.unpack("!h", body[:2])[0]):
            pos = body.index(b"\0", pos) + 1
            f = struct.unpack("!IhIhih", body[pos:pos + 18])
            oids.append(f[2]), fmts.append(f[5])
            pos += 18
        self.assertEqual(oids, [23, 20, 701, 16, 1700, 1082, 1184, 2950, 25])
        self.assertEqual(fmts, [1, 1, 1, 1, 1, 1, 1, 1, 0])
        i, b, f, ok, n, d, ts, u, arr = cells(got[3][1])
        self.assertEqual(struct.unpack("!i", i)[0], 7)
        self.assertEqual(struct.unpack("!q", b)[0], 9007199254740993)
        self.assertEqual(struct.unpack("!d", f)[0], 1.5)
        self.assertEqual(ok, b"\x01")
        self.assertEqual(n, struct.pack("!hhhh", 2, 0, 0x4000, 4) + struct.pack("!hh", 123, 4500))
        self.assertEqual(struct.unpack("!i", d)[0], (datetime.date(2024, 2, 29) - datetime.date(2000, 1, 1)).days)
        self.assertEqual(struct.unpack("!q", ts)[0], int((datetime.datetime(2026, 10, 21, 7, 0, 0, 500000) - datetime.datetime(2000, 1, 1)).total_seconds() * 1_000_000))
        self.assertEqual(uuid.UUID(bytes=u), uuid.UUID("123e4567-e89b-12d3-a456-426614174000"))
        self.assertEqual(arr, b"{1,2}")
        c.close()

    def test_maxrows_suspends_portal(self):
        c = self.connect()
        kinds, got = self.roundtrip(c, parse("", "select 1"), bind("", ""), describe("P", ""), execute("", 1), SYNC)
        self.assertEqual(kinds, "12TDsZ")
        kinds, got = self.roundtrip(c, execute("", 1), SYNC)
        self.assertEqual(kinds, "DCZ")
        c.close()

    def test_error_skips_to_sync(self):
        c = self.connect()
        kinds, got = self.roundtrip(c, parse("", "select * from nope"), bind("", ""), describe("P", ""), execute(""), SYNC)
        self.assertEqual(kinds, "12EZ")
        self.assertEqual(c.err(got[2][1])["C"], "42P01")
        kinds, got = self.roundtrip(c, parse("", "echo $9"), bind("", ""), execute(""), SYNC)
        self.assertEqual((kinds, c.err(got[1][1])["M"]), ("1EZ", "there is no parameter $9"))
        self.assertEqual(c.query("select 1")[0], "TDDCZ")
        c.close()

    def test_empty_and_close(self):
        c = self.connect()
        kinds, _ = self.roundtrip(c, parse("", "  "), bind("", ""), describe("P", ""), execute(""), pack(b"C", b"S\0"), SYNC)
        self.assertEqual(kinds, "12nI3Z")
        c.close()

    def test_codecs(self):
        self.assertEqual(types.numeric("0"), struct.pack("!hhhh", 0, 0, 0, 0))
        self.assertEqual(types.numeric("10000.01"), struct.pack("!hhhh", 3, 1, 0, 2) + struct.pack("!hhh", 1, 0, 100))
        self.assertEqual(types.encode("infinity", 1114), struct.pack("!q", 2**63 - 1))
        self.assertEqual(types.encode("2000-01-01 00:00:00", 1114), struct.pack("!q", 0))
        self.assertEqual(types.encode("12:34:56.5", 1083), struct.pack("!q", 45296500000))
        self.assertEqual(types.encode("\\x00ff", 17), b"\x00\xff")
        self.assertEqual(types.decode(struct.pack("!i", 9), 1082), "2000-01-10")
        self.assertEqual(types.decode(struct.pack("!q", 1_000_000), 1184), "2000-01-01 00:00:01+00:00")



if __name__ == "__main__":
    unittest.main()
