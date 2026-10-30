"""TCP listener for PostgreSQL clients: live config, the pgAdmin registry, open sessions and the idle reaper."""
import socketserver
import threading
import time

from .cache import Cache
from .registry import Registry
from .session import Session


class Server(socketserver.ThreadingTCPServer):
    """Listens for PG clients; holds the config, the pgAdmin registry and the connected sessions."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, cfg, cookie_dir, advertise=None):
        super().__init__(addr, Session)
        self.registry, self.conns, self.cfg, self.cache = Registry(cookie_dir), {}, {}, Cache()
        self.advertise = advertise  # host:port clients use, when it differs from the listen address (Docker)
        self.reload(cfg)
        threading.Thread(target=self.reap, daemon=True).start()

    def reload(self, cfg):
        """Applies a new config without a restart; open sessions keep their Query Tool."""
        self.cfg = cfg
        self.registry.reload(cfg["instances"], cfg.get("lanes", 4))
        self.cache.clear()

    def setup(self):
        """SET statements run at the start of every backend."""
        cfg, out = self.cfg, []
        if cfg["read_only"]:
            out.append("SET default_transaction_read_only = on")
        if cfg["timeout"]:
            out.append(f"SET statement_timeout = '{cfg['timeout']}s'")
        if cfg["idle"]:
            out.append(f"SET idle_in_transaction_session_timeout = '{cfg['idle']}s'")
        return out

    def status(self):
        """Live view for the web UI."""
        now = time.monotonic()
        return {
            "servers": self.registry.servers(),
            "cache": {"entries": len(self.cache.entries), "hits": self.cache.hits, "misses": self.cache.misses},
            "connections": [{"key": f"{k[0]}:{k[1]}", "client": f"{c.client_address[0]}:{c.client_address[1]}",
                             "target": c.name, "application": c.application, "backend": bool(c.tool.tid),
                             "in_tx": c.tool.tx != b"I", "idle_s": int(now - c.last), "age_s": int(now - c.started),
                             "queries": c.queries}
                            for k, c in list(self.conns.items())],
        }

    def reap(self):
        """Frees backends of connections silent for longer than idle and not inside a transaction."""
        while True:
            time.sleep(30)
            idle = self.cfg["idle"]
            for conn in list(self.conns.values()):
                with conn.lock:
                    if idle and conn.tool.tid and conn.tool.tx == b"I" and time.monotonic() - conn.last > idle:
                        conn.detach(f"idle {idle // 60} min")
