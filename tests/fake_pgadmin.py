"""A tiny HTTP server that answers like pgAdmin 7: login with CSRF, server tree, Query Tool with canned SQL."""
import http.server
import json
import threading
import urllib.parse

SETTINGS = ["16.4", "UTF8", "ISO, MDY", "UTC", "on", "on"]
ERR = 'ERROR:  relation "nope" does not exist\nLINE 1: select * from nope\n                      ^\nSQL state: 42P01\nCharacter: 15'


class Fake(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, servers, email="admin@example.com", password="secret"):
        super().__init__(("127.0.0.1", 0), Handler)
        self.servers, self.email, self.password = servers, email, password
        self.tools, self.calls, self.logins = {}, [], 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.route("GET", self.path, None)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Content-Type", "").startswith("application/json"):
            body = json.loads(raw or b"null")
        else:
            body = dict(urllib.parse.parse_qsl(raw.decode()))
        self.route("POST", self.path, body)

    def do_DELETE(self):
        self.route("DELETE", self.path, None)

    def route(self, method, path, body):
        srv = self.server
        srv.calls.append((method, path, body))
        tid = path.rstrip("/").split("/")[-1]
        authed = "pga4_session=ok" in (self.headers.get("Cookie") or "")
        if path == "/login":
            return self.html('<script>csrfToken": "tok123"</script>')
        if path == "/authenticate/login":
            srv.logins += 1
            ok = body.get("csrf_token") == "tok123" and (body.get("email"), body.get("password")) == (srv.email, srv.password)
            return self.redirect("/browser/" if ok else "/login", "pga4_session=ok" if ok else None)
        if not authed:
            return self.redirect("/login")
        if path == "/browser/js/utils.js":
            return self.html("pgAdmin['csrf_token'] = 'csrf456';")
        if self.headers.get("X-pgA-CSRFToken") != "csrf456" and method != "GET":
            return self.json(400, {"success": 0, "errormsg": "CSRF token missing"})
        if path == "/browser/server_group/nodes/":
            return self.json(200, {"success": 1, "data": [{"_id": 1, "label": "Servers"}]})
        if path == "/browser/server/nodes/1/":
            return self.json(200, {"success": 1, "data": srv.servers})
        if path.startswith("/browser/server/connect/"):
            return self.json(200, {"success": 1, "data": {"connected": True}})
        if path.startswith("/sqleditor/initialize/sqleditor/"):
            srv.tools[path.split("/")[4]] = {"db": body.get("dbname") or "postgres", "tx": 0, "sql": None, "polled": 0}
            return self.json(200, {"success": 1, "data": {"connId": tid}})
        if path.startswith("/sqleditor/close/"):
            srv.tools.pop(tid, None)
            return self.json(200, {"success": 1, "data": {}})
        tool = srv.tools.get(tid)
        if path.startswith("/sqleditor/") and tool is None:
            return self.json(404, {"success": 0, "errormsg": "Transaction ID not found in the session.",
                                   "info": "DATAGRID_TRANSACTION_REQUIRED"})
        if path.startswith("/sqleditor/query_tool/start/"):
            tool.update(sql=body["sql"], polled=0)
            return self.json(200, {"success": 1, "data": {"status": True, "result": None}})
        if path.startswith("/sqleditor/poll/"):
            tool["polled"] += 1
            if tool["polled"] == 1:
                return self.json(200, {"success": 1, "data": {"status": "Busy"}})
            st, data = self.answer(tool)
            return self.json(st, data)
        if path.startswith("/sqleditor/fetch/"):
            tool["fetched"] = tool.get("fetched", 0) + 1
            rows = [[i] for i in range(1001, 1501)] if tool["fetched"] == 1 else None
            return self.json(200, {"success": 1, "data": {"result": rows, "has_more_rows": False}})
        return self.json(200, {"success": 1, "data": {}})

    def answer(self, tool):
        """Canned results keyed by the last statement of the batch."""
        sql = tool["sql"].strip().rstrip(";").split(";\n")[-1].strip().lower()
        tx = tool["tx"]

        def ok(**kw):
            return 200, {"success": 1, "data": dict({"status": "Success", "transaction_status": tool["tx"]}, **kw)}

        def col(name, oid, **kw):
            return dict({"name": name, "type_code": oid, "table_oid": None, "table_column": None}, **kw)

        if sql.startswith("echo "):
            return ok(colinfo=[col("sql", 25)], result=[[tool["sql"]]])
        if sql.startswith("select typed"):
            return ok(colinfo=[col("i", 23), col("b", 20), col("f", 701), col("ok", 16), col("n", 1700), col("d", 1082),
                               col("ts", 1184), col("u", 2950), col("arr", 1007)],
                      result=[[7, 9007199254740993, 1.5, True, "-123.4500", "2024-02-29", "2026-10-21 12:00:00.5+05",
                               "123e4567-e89b-12d3-a456-426614174000", "{1,2}"]])
        if sql.startswith("select current_database()"):
            return ok(colinfo=[col(f"c{i}", 25) for i in range(7)], result=[[tool["db"]] + SETTINGS])
        if sql.startswith("set "):
            return ok(colinfo=None, result="SET")
        if sql in ("begin", "commit", "rollback"):
            tool["tx"] = 2 if sql == "begin" else 0
            return ok(colinfo=None, result=sql.upper())
        if sql.startswith("select 1"):
            return ok(colinfo=[col("n", 23), col("a", 16), col("a-2", 25, display_name="a")],
                      result=[[1, True, None], [2, False, "x"]])
        if sql.startswith("select empty"):
            return ok(colinfo=[col("id", 20)], result="SELECT 0")
        if sql.startswith("update"):
            return ok(colinfo=None, result="UPDATE 3", rows_affected=3)
        if sql.startswith("do"):
            return ok(colinfo=None, result="NOTICE:  hello\nDO", additional_messages="NOTICE:  hello\n")
        if sql.startswith("select many"):
            return ok(colinfo=[col("n", 23)], result=[[i] for i in range(1, 1001)], has_more_rows=True)
        if sql.startswith("select * from nope"):
            if tx == 2:
                tool["tx"] = 3
            return 500, {"success": 0, "errormsg": ERR, "info": None, "data": {"transaction_status": tool["tx"]}}
        return ok(colinfo=None, result="UNKNOWN " + sql)

    def json(self, status, data):
        self.reply(status, json.dumps(data).encode(), "application/json")

    def html(self, text):
        self.reply(200, text.encode(), "text/html")

    def redirect(self, location, cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie + "; Path=/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def reply(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
