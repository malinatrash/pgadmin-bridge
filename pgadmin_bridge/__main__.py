"""pgadmin-bridge: connect any PostgreSQL client to databases you can only reach through pgAdmin.

  pgadmin-bridge                                  PG on 127.0.0.1:5432, web UI on 127.0.0.1:8080
  pgadmin-bridge --host 0.0.0.0 --port 5555 --web-port 8081
  PGADMIN_BRIDGE_DATA=/data pgadmin-bridge        where config.json and cookies live (default ./data)
"""
import argparse
import threading

from . import __version__, config, proxy, web
from .log import log


def main():
    p = argparse.ArgumentParser(prog="pgadmin-bridge", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="address for both listeners")
    p.add_argument("--port", type=int, default=5432, help="PostgreSQL wire port")
    p.add_argument("--web-port", type=int, default=8080, help="web UI port")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args()

    cfg = config.load()
    srv = proxy.Server((a.host, a.port), cfg, config.DATA)
    ui = web.Server((a.host, a.web_port), srv)
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    log(f"pgadmin-bridge {__version__}: postgres on {a.host}:{a.port}, "
        f"web UI on http://{a.host}:{a.web_port}, data in {config.DATA}")
    if not cfg["instances"]:
        log("no pgAdmin instances yet: add one in the web UI")
    else:
        srv.registry.scan()
        log(f"User = pgAdmin server: {srv.registry.names()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
