"""End-to-end: a raw wire client (and psql when installed) through the proxy into the fake pgAdmin."""
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pgadmin_bridge.proxy.registry import Registry  # noqa: E402

from fake_pgadmin import Fake  # noqa: E402
from helpers import SERVERS, Base, pack  # noqa: E402


class ProxyTest(Base):
    def test_login_and_parameters(self):
        c = self.connect()
        self.assertEqual(c.params["server_version"], "16.4")
        self.assertEqual(self.fake.logins, 1)
        c.close()

    def test_rows_types_and_errors(self):
        c = self.connect()
        kinds, tx, rows, err = c.query("select 1")
        self.assertEqual((kinds, tx, rows), ("TDDCZ", "I", [["1", "t", None], ["2", "f", "x"]]))
        self.assertEqual(c.query("select empty")[2], [])
        self.assertEqual(c.query("update t set x = 1")[0], "CZ")
        self.assertIn("N", c.query("do $$ begin end $$")[0])
        self.assertEqual(len(c.query("select many")[2]), 1500)
        kinds, tx, rows, err = c.query("select * from nope")
        self.assertEqual((kinds, err["C"], err["P"]), ("EZ", "42P01", "15"))
        self.assertEqual(c.query("")[0], "IZ")
        c.close()

    def test_transaction_status(self):
        c = self.connect()
        self.assertEqual(c.query("begin")[1], "T")
        self.assertEqual(c.query("select * from nope")[1], "E")
        self.assertEqual(c.query("rollback")[1], "I")
        c.close()

    def test_function_call_is_rejected(self):
        c = self.connect()
        c.s.sendall(pack(b"F", b"\0" * 10) + pack(b"S", b""))
        kinds = "".join(k.decode() for k, _ in c.until_ready())
        self.assertEqual(kinds, "EZ")
        self.assertEqual(c.query("select 1")[0], "TDDCZ")
        c.close()

    def test_bad_password_server_and_instance_prefix(self):
        with self.assertRaises(ConnectionError) as e:
            self.connect(password="nope")
        self.assertEqual(e.exception.args[0]["C"], "28P01")
        with self.assertRaises(ConnectionError) as e:
            self.connect(user="missing")
        self.assertIn("proxytest/main", e.exception.args[0]["M"])
        c = self.connect(user="ProxyTest/other", db="")
        self.assertIn(("POST", "/browser/server/connect/1/8", {}), self.fake.calls)
        c.close()

    def test_first_lookup_scans_right_after_boot(self):
        """time.monotonic() is the uptime on Linux: a fresh CI VM is less than a minute old."""
        fake = Fake(SERVERS)
        registry = Registry(None)
        registry.reload([dict(self.srv.cfg["instances"][0], url=fake.url)])
        with mock.patch("pgadmin_bridge.proxy.registry.time.monotonic", return_value=5.0):
            self.assertEqual(registry.locate("main")[1]["label"], "main")

    def test_idle_release_replays_sets(self):
        c = self.connect()
        c.query("SET search_path TO app")
        c.query("SET application_name = 'a'")
        c.query("SET application_name = 'b'")
        conn = next(iter(self.srv.conns.values()))
        self.assertEqual(conn.sets, ["SET search_path TO app", "SET application_name = 'b'"])
        with conn.lock:
            conn.detach("test")
        before = len(self.fake.calls)
        self.assertEqual(c.query("select 1")[0], "TDDCZ")
        starts = [b["sql"] for m, p, b in self.fake.calls[before:] if "query_tool/start" in p]
        self.assertIn("SET statement_timeout = '300s';\nSET idle_in_transaction_session_timeout = '600s';\nSET search_path TO app;\nSET application_name = 'b';\nselect current_database()", starts[0])
        c.close()

    @unittest.skipUnless(shutil.which("psql"), "psql not installed")
    def test_psql(self):
        r = subprocess.run(["psql", f"host=127.0.0.1 port={self.port} user=main dbname=app", "-X", "-At",
                            "-c", "select 1", "-c", "select * from nope"],
                           capture_output=True, text=True, env=dict(os.environ, PGPASSWORD="pw"), timeout=30)
        self.assertEqual(r.stdout, "1|t|\n2|f|x\n")
        self.assertIn('relation "nope" does not exist', r.stderr)


if __name__ == "__main__":
    unittest.main()
