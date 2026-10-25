"""pgAdmin clients for the configured instances and the servers their trees expose."""
import time

from ..log import log
from ..pgadmin import ApiError, PgAdmin

AMBIGUOUS = "ambiguous"


class Registry:
    """Maps a client's User ('server' or 'instance/server') to a pgAdmin client and server node."""

    def __init__(self, cookie_dir):
        self.cookie_dir, self.pgs, self.nodes, self.scanned = cookie_dir, {}, {}, 0

    def reload(self, instances):
        """Keeps the pgAdmin client (and its login) of unchanged instances; the tree is rescanned on next use."""
        old = self.pgs
        self.pgs = {i["name"]: old[i["name"]] if _same(old.get(i["name"]), i) else PgAdmin(i, self.cookie_dir)
                    for i in instances}
        self.nodes, self.scanned = {}, 0

    def locate(self, user):
        """-> (pgAdmin client, server node); rescans the trees on a miss, at most once a minute."""
        key = user.lower()
        node = self.nodes.get(key)
        if not node and time.monotonic() - self.scanned > 60:
            node = self.scan().get(key)
        if node == AMBIGUOUS:
            raise ApiError(f"'{user}' exists in several pgAdmin instances; use User = instance/server")
        if not node:
            raise ApiError(f"no pgAdmin server '{user}'; use one of: {self.names()}")
        return self.pgs[node["inst"]], node

    def scan(self):
        self.scanned, self.nodes = time.monotonic(), {}
        for name, pg in self.pgs.items():
            try:
                for s in pg.servers():
                    node = dict(s, inst=name)
                    self.nodes[f"{name}/{s['label']}".lower()] = node
                    self.nodes[s["label"].lower()] = AMBIGUOUS if s["label"].lower() in self.nodes else node
            except ApiError as e:
                log(f"[!] pgAdmin {name}: {e}")
        return self.nodes

    def names(self):
        return ", ".join(sorted(k for k, n in self.nodes.items() if n != AMBIGUOUS and "/" in k))

    def servers(self):
        """Discovered servers for the web UI, with whether the bare name is unique."""
        nodes = {id(n): n for n in self.nodes.values() if n != AMBIGUOUS}.values()
        return sorted(({"instance": n["inst"], "server": n["label"],
                        "unique": self.nodes.get(n["label"].lower()) is n} for n in nodes),
                      key=lambda s: (s["instance"].lower(), s["server"].lower()))


def _same(pg, inst):
    return pg and (pg.base, pg.email, pg.password) == (inst["url"].rstrip("/"), inst["email"], inst["password"])
