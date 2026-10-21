"""Settings file in the data dir: read on start, rewritten by the web UI."""
import json
import os
import threading

DATA = os.environ.get("PGPROXY_DATA", "data")
PATH = os.path.join(DATA, "config.json")
DEFAULT = {"password": "", "read_only": False, "timeout": 0, "idle": 600, "instances": []}
INSTANCE = {"name": "", "url": "", "email": "", "password": "", "verify_tls": True}
LOCK = threading.Lock()


def load():
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    try:
        with open(PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    cfg = dict(DEFAULT, **{k: raw[k] for k in DEFAULT if k in raw})
    cfg["instances"] = [dict(INSTANCE, **{k: i[k] for k in INSTANCE if k in i}) for i in cfg["instances"]]
    return cfg


def save(cfg):
    with LOCK:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, PATH)


def check(cfg):
    """Validation for what the web UI sends; -> error text or None."""
    if not isinstance(cfg.get("instances"), list):
        return "instances must be a list"
    names = [i.get("name", "") for i in cfg["instances"]]
    if any(not n for n in names) or len(set(names)) != len(names):
        return "every pgAdmin instance needs a unique name"
    if any(not str(i.get("url", "")).startswith(("http://", "https://")) for i in cfg["instances"]):
        return "instance url must start with http:// or https://"
    for k in ("timeout", "idle"):
        if not isinstance(cfg.get(k), int) or cfg[k] < 0:
            return f"{k} must be a non-negative integer"
    return None
