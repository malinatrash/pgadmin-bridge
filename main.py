#!/usr/bin/env python3
"""pgproxy: PostgreSQL wire proxy over pgAdmin's REST API, configured from a small web UI.

  main.py                                   PG on 127.0.0.1:5432, web UI on 127.0.0.1:8080
  main.py --host 0.0.0.0 --port 5555 --web-port 8081
  PGPROXY_DATA=/data main.py                where config.json and cookies live (default ./data)
"""
import argparse
import threading

import config
import proxy
import web
from log import log


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="address for both listeners")
    p.add_argument("--port", type=int, default=5432, help="PostgreSQL wire port")
    p.add_argument("--web-port", type=int, default=8080, help="web UI port")
    a = p.parse_args()

    cfg = config.load()
    srv = proxy.Server((a.host, a.port), cfg, config.DATA)
    ui = web.Server((a.host, a.web_port), srv)
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    log(f"pgproxy: postgres on {a.host}:{a.port}, web UI on http://{a.host}:{a.web_port}, data in {config.DATA}")
    if not cfg["instances"]:
        log("no pgAdmin instances yet: add one in the web UI")
    else:
        srv.scan()
        log(f"User = pgAdmin server: {srv.names()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
