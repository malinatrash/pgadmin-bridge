"""HTTP server for the web UI: static files from ./static and the JSON API from api.py."""
import http.server
import json
import mimetypes
import os
import urllib.parse

from ..pgadmin import ApiError
from . import api

STATIC = os.path.join(os.path.dirname(os.path.realpath(__file__)), "static")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, proxy):
        super().__init__(addr, Handler)
        self.proxy = proxy


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in api.GET:
            return self.json(api.GET[path](self.server.proxy))
        self.static("index.html" if path == "/" else path.lstrip("/"))

    def do_POST(self):
        handler = api.POST.get(urllib.parse.urlparse(self.path).path)
        if not handler:
            return self.reply(404, b"not found")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            self.json(handler(self.server.proxy, body))
        except (ApiError, ValueError, KeyError, OSError) as e:
            self.json({"error": str(e)}, 400)

    def static(self, name):
        path = os.path.realpath(os.path.join(STATIC, name))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self.reply(404, b"not found")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            self.reply(200, f.read(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))

    def json(self, data, status=200):
        self.reply(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def reply(self, status, body, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
