"""End-to-end: a raw wire client (and psql when installed) through the proxy into the fake pgAdmin."""
import os
import shutil
import subprocess
import sys
import time
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
        self.assertLessEqual(self.fake.logins, self.srv.cfg["lanes"])
        c.close()

    def test_rows_types_and_errors(self):
        c = self.connect()
        kinds, tx, rows, err = c.query("select two")
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
        self.assertEqual(c.query("select two")[0], "TDDCZ")
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
        c.query("SET TimeZone = 'UTC'")
        c.query("SET TimeZone = 'Asia/Almaty'")
        conn = next(c for c in self.srv.conns.values() if c.sets)
        self.assertEqual(conn.sets, ["SET search_path TO app", "SET TimeZone = 'Asia/Almaty'"])
        with conn.lock:
            conn.detach("test")
        before = len(self.fake.calls)
        self.assertEqual(c.query("select two")[0], "TDDCZ")
        starts = [b["sql"] for m, p, b in self.fake.calls[before:] if "query_tool/start" in p]
        self.assertIn("SET statement_timeout = '300s';\nSET idle_in_transaction_session_timeout = '600s';\nSET search_path TO app;\nSET TimeZone = 'Asia/Almaty';\nselect current_database()", starts[0])
        c.close()

    def test_local_answers_do_not_reach_pgadmin(self):
        c = self.connect()
        conn = next(cn for cn in self.srv.conns.values() if cn.tool.tid)
        with conn.lock:
            conn.detach("test")
        before = len(self.fake.calls)
        self.assertEqual(c.query("select 1")[2], [["1"]])
        self.assertEqual(c.query("SELECT 1;")[0], "TDCZ")
        self.assertEqual(c.query("select version()")[2], [["PostgreSQL 16.4 (fake)"]])
        self.assertEqual(c.query("show server_version")[2], [["16.4"]])
        self.assertEqual(c.query("SHOW client_encoding")[2], [["UTF8"]])
        self.assertEqual(c.query("SET application_name = 'dg'")[0], "CZ")
        self.assertEqual(c.query("set extra_float_digits to 3")[0], "CZ")
        self.assertEqual(len(self.fake.calls), before, "no HTTP call for local answers")
        self.assertEqual(conn.application, "dg")
        self.assertIsNone(conn.tool.tid, "the released backend stays released")
        self.assertEqual(c.query("select two")[0], "TDDCZ")
        c.close()

    def test_keep_alive_and_gzip(self):
        before = self.fake.sockets
        c = self.connect()
        for _ in range(5):
            c.query("select many")
        self.assertLessEqual(self.fake.sockets - before, self.srv.cfg["lanes"] + 1, "one TCP connection per lane, not per request")
        c.close()

    def test_fetch_all_with_fallback(self):
        c = self.connect()
        before = len(self.fake.calls)
        self.assertEqual(len(c.query("select many")[2]), 1500)
        self.assertEqual([p for m, p, b in self.fake.calls[before:] if "/fetch/" in p][0].count("/"), 4)
        pg = self.srv.registry.pgs[self.__class__.__name__]
        self.assertTrue(pg.fetch_all)
        self.fake.fetch_all, pg.fetch_all = False, None
        try:
            before = len(self.fake.calls)
            self.assertEqual(len(c.query("select many")[2]), 1500)
            self.assertEqual(len([p for m, p, b in self.fake.calls[before:] if "/fetch/" in p]), 3)
            self.assertFalse(pg.fetch_all)
        finally:
            self.fake.fetch_all = True
            pg.fetch_all = None
        c.close()

    def test_catalog_cache_and_coalescing(self):
        self.srv.cfg["cache"] = 60
        self.srv.cache.clear()
        try:
            a, b = self.connect(), self.connect()
            sql = "select two from pg_catalog.pg_class"
            before = len(self.fake.calls)
            self.assertEqual(a.query(sql)[2], b.query(sql)[2])
            self.assertEqual(len([p for m, p, b_ in self.fake.calls[before:] if "query_tool/start" in p]), 1)
            self.assertEqual((self.srv.cache.hits, self.srv.cache.misses), (1, 1))
            self.assertEqual(a.query("select two from pg_stat_activity")[0], "TDDCZ")
            self.assertEqual(self.srv.cache.misses, 1, "volatile queries are not cached")
            a.close(), b.close()
        finally:
            self.srv.cfg["cache"] = 0
            self.srv.cache.clear()

    def test_lanes_run_in_parallel(self):
        pg = self.srv.registry.pgs[self.__class__.__name__]
        conns = [self.connect() for _ in range(3)]
        lanes = {cn.tool.lane.index for cn in self.srv.conns.values() if cn.tool.tid}
        self.assertGreater(len(lanes), 1, "clients are spread over lanes")
        self.assertEqual(sum(ln.tools for ln in pg.lanes), 3)
        for c in conns:
            c.close()
        for _ in range(100):
            if not self.srv.conns:
                break
            time.sleep(0.02)
        self.assertEqual(sum(ln.tools for ln in pg.lanes), 0)

    @unittest.skipUnless(shutil.which("psql"), "psql not installed")
    def test_psql(self):
        r = subprocess.run(["psql", f"host=127.0.0.1 port={self.port} user=main dbname=app", "-X", "-At",
                            "-c", "select two", "-c", "select * from nope"],
                           capture_output=True, text=True, env=dict(os.environ, PGPASSWORD="pw"), timeout=30)
        self.assertEqual(r.stdout, "1|t|\n2|f|x\n")
        self.assertIn('relation "nope" does not exist', r.stderr)


if __name__ == "__main__":
    unittest.main()
