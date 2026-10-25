"""JSON API of the web UI. Every handler takes the proxy server and the request body and returns a dict."""
import socket

from .. import __version__, config, log
from ..pgadmin import PgAdmin

MASK = "••••••••"


def state(proxy, body=None):
    cfg = proxy.cfg
    shown = dict(cfg, password=MASK if cfg["password"] else "",
                 instances=[dict(i, password=MASK if i["password"] else "") for i in cfg["instances"]])
    host, port = proxy.server_address[:2]
    if proxy.advertise:
        host, _, port = proxy.advertise.rpartition(":")
    return dict(proxy.status(), config=shown, version=__version__, listen={"host": host, "port": int(port)})


def logs(proxy, body=None):
    return {"lines": log.tail()}


def save(proxy, body):
    """Masked passwords mean "unchanged" (an instance may carry its previous name in 'was'); validated, written, applied."""
    old = proxy.cfg
    cfg = {k: body.get(k, old[k]) for k in config.DEFAULT}
    if cfg["password"] == MASK:
        cfg["password"] = old["password"]
    if not isinstance(cfg["instances"], list):
        raise ValueError("instances must be a list")
    known = {i["name"]: i["password"] for i in old["instances"]}
    instances = []
    for raw in cfg["instances"]:
        inst = config.instance(raw)
        if inst["password"] == MASK:
            inst["password"] = known.get(raw.get("was") or inst["name"], "")
        instances.append(inst)
    cfg["instances"] = instances
    err = config.check(cfg)
    if err:
        raise ValueError(err)
    config.save(cfg)
    proxy.reload(cfg)
    log.log("config saved from the web UI")
    return rescan(proxy)


def test(proxy, body):
    """Logs in to one instance (as entered, not yet saved) and lists its servers."""
    inst = config.instance(body)
    if inst["password"] == MASK:
        name = body.get("was") or inst["name"]
        inst["password"] = next((i["password"] for i in proxy.cfg["instances"] if i["name"] == name), "")
    err = config.check({**config.DEFAULT, "instances": [inst]})
    if err:
        raise ValueError(err)
    return {"servers": [s["label"] for s in PgAdmin(inst).servers()]}


def rescan(proxy, body=None):
    proxy.registry.scan()
    return state(proxy)


def kill(proxy, body):
    """Closes a client connection; its Query Tool is released by the session thread."""
    for key, conn in list(proxy.conns.items()):
        if f"{key[0]}:{key[1]}" == body.get("key"):
            conn.request.shutdown(socket.SHUT_RDWR)
    return state(proxy)


GET = {"/api/state": state, "/api/log": logs}
POST = {"/api/config": save, "/api/test": test, "/api/rescan": rescan, "/api/kill": kill}
