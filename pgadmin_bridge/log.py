"""Timestamped stdout log kept in a ring buffer for the web UI."""
import collections
import threading
import time

LINES = collections.deque(maxlen=2000)
LOCK = threading.Lock()


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with LOCK:
        print(line, flush=True)
        LINES.append(line)


def tail(n=500):
    with LOCK:
        return list(LINES)[-n:]


def elapsed(start):
    """Milliseconds since a time.monotonic() value, padded for aligned log columns."""
    return f"{(time.monotonic() - start) * 1000:6.0f}ms"
