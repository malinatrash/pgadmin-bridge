"""pgAdmin clients for the configured instances and the servers their trees expose."""
import threading
import time

from ..log import log
from ..pgadmin import ApiError, PgAdmin

AMBIGUOUS = "ambiguous"
NEVER = float("-inf")


class Registry:
    """Maps a client's User ('server' or 'instance/server') to a pgAdmin client and server node."""

    def __init__(self, cookie_dir):
        # "never scanned" must be long ago even on a machine booted less than a minute ago (monotonic ~ uptime)
        self.cookie_dir, self.pgs, self.nodes, self.scanned = cookie_dir, {}, {}, NEVER
        self.scanning = threading.RLock()  # one scan at a time; lookups that miss wait for a scan in progress

    def reload(self, instances, lanes=4):
        """Keeps the pgAdmin client (and its logins) of unchanged instances; the tree is rescanned on next use."""
        old = self.pgs
        self.pgs = {i["name"]: old[i["name"]] if _same(old.get(i["name"]), i, lanes) else PgAdmin(i, self.cookie_dir, lanes)
                    for i in instances}
        self.nodes, self.scanned = {}, NEVER

    def locate(self, user):
        """-> (pgAdmin client, server node); rescans the trees on a miss, at most once a minute."""
        key = user.lower()
        node = self.nodes.get(key)
        if not node:
            with self.scanning:
                node = self.nodes.get(key)  # a scan that was running when we missed may have found it
                if not node and time.monotonic() - self.scanned > 60:
                    node = self.scan().get(key)
        if node == AMBIGUOUS:
            raise ApiError(f"'{user}' exists in several pgAdmin instances; use User = instance/server")
        if not node:
            raise ApiError(f"no pgAdmin server '{user}'; use one of: {self.names()}")
        return self.pgs[node["inst"]], node

    def scan(self):
        """Walks the trees of all instances at the same time; -> the new lookup table."""
        with self.scanning:
            return self._scan()

    def _scan(self):
        self.scanned, found = time.monotonic(), {}

        def walk(name, pg):
            try:
                found[name] = pg.servers()
            except ApiError as e:
                log(f"[!] pgAdmin {name}: {e}")

        threads = [threading.Thread(target=walk, args=item, daemon=True) for item in self.pgs.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        nodes = {}
        for name in self.pgs:
            for s in found.get(name, []):
                node = dict(s, inst=name)
                nodes[f"{name}/{s['label']}".lower()] = node
                nodes[s["label"].lower()] = AMBIGUOUS if s["label"].lower() in nodes else node
        self.nodes = nodes
        return nodes

    def names(self):
        return ", ".join(sorted(k for k, n in self.nodes.items() if n != AMBIGUOUS and "/" in k))

    def servers(self):
        """Discovered servers for the web UI, with whether the bare name is unique."""
        nodes = {id(n): n for n in self.nodes.values() if n != AMBIGUOUS}.values()
        return sorted(({"instance": n["inst"], "server": n["label"],
                        "unique": self.nodes.get(n["label"].lower()) is n} for n in nodes),
                      key=lambda s: (s["instance"].lower(), s["server"].lower()))


def _same(pg, inst, lanes):
    now = (inst["url"].rstrip("/"), inst["email"], inst["password"], lanes)
    return pg and (pg.base, pg.email, pg.password, len(pg.lanes)) == now
