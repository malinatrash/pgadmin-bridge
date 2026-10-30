"""Short-lived cache of catalog query results shared by all sessions, with coalescing of identical in-flight queries."""
import re
import threading
import time

CATALOG = re.compile(r"\b(pg_catalog\.|information_schema\.|pg_(class|namespace|attribute|attrdef|type|proc|constraint|index"
                     r"|description|shdescription|database|roles|authid|user|tablespace|am|collation|enum|range|extension"
                     r"|depend|trigger|rewrite|inherits|language|operator|opclass|opfamily|cast|conversion|settings"
                     r"|available_extensions|policy|publication|subscription|sequence|partitioned_table|foreign_\w+"
                     r"|event_trigger|aggregate|get_\w+|table_is_visible|\w+_is_visible)\b)", re.I)
VOLATILE = re.compile(r"\b(pg_stat\w*|pg_lock\w*|pg_sleep\w*|nextval|setval|now|current_timestamp|clock_timestamp"
                      r"|statement_timestamp|transaction_timestamp|pg_backend_pid|txid_\w+|pg_current_\w+|pg_last_\w+"
                      r"|random|gen_random_uuid|pg_advisory\w*|pg_try_advisory\w*|pg_notify|pg_cancel_backend"
                      r"|pg_terminate_backend|pg_reload_conf|pg_rotate_logfile|pg_is_in_recovery|pg_postmaster_start_time"
                      r"|pg_conf_load_time|pg_blocking_pids|pg_cursors|pg_prepared_\w+|pg_listening_channels"
                      r"|pg_relation_size|pg_total_relation_size|pg_table_size|pg_indexes_size|pg_database_size"
                      r"|pg_tablespace_size|pg_size_pretty|txid|current_setting|set_config|for\s+(update|share|no key)"
                      r"|into|insert|update|delete|create|alter|drop|truncate|grant|revoke|lock|copy|call|do|refresh"
                      r"|vacuum|analyze|cluster|reindex|listen|notify|execute|prepare|deallocate|declare|fetch|move)\b", re.I)
MAX_ROWS, MAX_ENTRIES = 5000, 500


def cacheable(sql):
    """A read-only statement over the system catalogs with nothing volatile in it."""
    s = sql.strip().rstrip(";")
    return bool(re.match(r"(select|with)\b", s, re.I)) and ";" not in s and bool(CATALOG.search(s)) and not VOLATILE.search(s)


class Cache:
    """get(key, ttl, fn) -> value, from the cache while fresh; concurrent callers of the same key share one fn().

    fn returns (value, keep): keep=False hands the value out without storing it (a result too big to cache).
    """

    def __init__(self):
        self.lock, self.entries, self.inflight = threading.Lock(), {}, {}
        self.hits = self.misses = 0

    def get(self, key, ttl, fn):
        now = time.monotonic()
        with self.lock:
            hit = self.entries.get(key)
            if hit and now - hit[0] <= ttl:
                self.hits += 1
                self.entries[key] = self.entries.pop(key)  # most recently used goes last for the LRU trim
                return hit[1]
            self.entries.pop(key, None)
            waiter = self.inflight.get(key)
            if waiter is None:
                waiter = self.inflight[key] = _Pending()
                leader = True
            else:
                leader = False
        if not leader:
            try:
                value = waiter.wait()
            except Exception:  # the leader's backend failed: that is its problem, run the statement ourselves
                return fn()[0]
            self.hits += 1
            return value
        try:
            value, keep = fn()
        except BaseException as e:
            with self.lock:
                self.inflight.pop(key, None)
            waiter.fail(e)
            raise
        with self.lock:
            self.misses += 1
            self.inflight.pop(key, None)
            if keep:
                self.entries[key] = (time.monotonic(), value)
                while len(self.entries) > MAX_ENTRIES:
                    self.entries.pop(next(iter(self.entries)))
        waiter.done(value)
        return value

    def clear(self):
        with self.lock:
            self.entries.clear()


class _Pending:
    def __init__(self):
        self.ev, self.value, self.err = threading.Event(), None, None

    def done(self, value):
        self.value = value
        self.ev.set()

    def fail(self, err):
        self.err = err
        self.ev.set()

    def wait(self):
        self.ev.wait()
        if self.err:
            raise self.err
        return self.value
