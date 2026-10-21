"""Web UI: settings and pgAdmin instances, live connections, log tail. JSON API under /api/."""
import http.server
import json
import os
import socket
import urllib.parse

import config
import log
from pgadmin import ApiError, PgAdmin

MASK = "••••••••"
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, proxy):
        super().__init__(addr, Handler)
        self.proxy = proxy


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            with open(UI, "rb") as f:
                return self.reply(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self.json(self.state())
        if path == "/api/log":
            return self.json({"lines": log.tail()})
        self.reply(404, b"not found")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            handler = {"/api/config": self.save, "/api/test": self.test, "/api/rescan": self.rescan,
                       "/api/kill": self.kill}.get(self.path)
            if not handler:
                return self.reply(404, b"not found")
            self.json(handler(body))
        except (ApiError, ValueError, KeyError, OSError) as e:
            self.json({"error": str(e)}, 400)

    def state(self):
        cfg = self.proxy.cfg
        shown = dict(cfg, password=MASK if cfg["password"] else "",
                     instances=[dict(i, password=MASK if i["password"] else "") for i in cfg["instances"]])
        return dict(self.proxy.status(), config=shown)

    def save(self, body):
        """Masked passwords mean "unchanged"; everything is validated, written to disk and applied."""
        old = self.proxy.cfg
        cfg = {k: body.get(k, old[k]) for k in config.DEFAULT}
        if cfg["password"] == MASK:
            cfg["password"] = old["password"]
        known = {i["name"]: i["password"] for i in old["instances"]}
        cfg["instances"] = [dict(config.INSTANCE, **{k: i.get(k, config.INSTANCE[k]) for k in config.INSTANCE})
                            for i in cfg["instances"]]
        for i in cfg["instances"]:
            if i["password"] == MASK:
                i["password"] = known.get(i["name"], "")
        err = config.check(cfg)
        if err:
            raise ValueError(err)
        config.save(cfg)
        self.proxy.reload(cfg)
        log.log("config saved from the web UI")
        return self.rescan({})

    def test(self, body):
        """Logs in to one instance (as entered, not yet saved) and lists its servers."""
        inst = dict(config.INSTANCE, **{k: body.get(k, config.INSTANCE[k]) for k in config.INSTANCE})
        if inst["password"] == MASK:
            inst["password"] = next((i["password"] for i in self.proxy.cfg["instances"] if i["name"] == inst["name"]), "")
        err = config.check({**config.DEFAULT, "instances": [inst]})
        if err:
            raise ValueError(err)
        servers = PgAdmin(inst).servers()
        return {"servers": [s["label"] for s in servers]}

    def rescan(self, body):
        self.proxy.scan()
        return self.state()

    def kill(self, body):
        """Closes a client connection; its Query Tool is released by the handler thread."""
        for key, conn in list(self.proxy.conns.items()):
            if f"{key[0]}:{key[1]}" == body.get("key"):
                conn.request.shutdown(socket.SHUT_RDWR)
        return self.state()

    def json(self, data, status=200):
        self.reply(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def reply(self, status, body, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    @property
    def proxy(self):
        return self.server.proxy
