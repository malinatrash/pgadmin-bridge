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


def tail(n=300):
    with LOCK:
        return list(LINES)[-n:]
