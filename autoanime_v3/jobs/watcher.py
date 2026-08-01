"""Pure event coalescing used by the watchdog producer."""

from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.scanner import INCOMPLETE_SUFFIXES


def utc_now():
    return datetime.now(timezone.utc)


class StableFileBuffer:
    def __init__(self, clock=None, debounce_seconds=2, stability_seconds=30):
        self.clock = clock or utc_now
        self.debounce_seconds = debounce_seconds
        self.stability_seconds = stability_seconds
        self.pending = {}

    def record(self, path, size, mtime_ns):
        value = Path(path)
        if value.name.casefold().endswith(tuple(suffix.casefold() for suffix in INCOMPLETE_SUFFIXES)):
            return
        key = str(value).casefold()
        now = self.clock()
        previous = self.pending.get(key)
        stable_since = now if previous is None or previous[1:3] != (size, mtime_ns) else previous[3]
        self.pending[key] = (value, size, mtime_ns, stable_since, now)

    def refresh(self, path, size, mtime_ns):
        value = Path(path)
        key = str(value).casefold()
        previous = self.pending.get(key)
        if previous is None:
            return
        now = self.clock()
        stable_since = previous[3] if previous[1:3] == (size, mtime_ns) else now
        self.pending[key] = (value, size, mtime_ns, stable_since, previous[4])

    def discard(self, path):
        self.pending.pop(str(Path(path)).casefold(), None)

    def paths(self):
        return tuple(item[0] for item in self.pending.values())

    def ready(self):
        now = self.clock()
        ready = []
        for key, (path, size, mtime_ns, stable_since, last_event) in list(self.pending.items()):
            if (
                (now - last_event).total_seconds() >= self.debounce_seconds
                and (now - stable_since).total_seconds() >= self.stability_seconds
            ):
                ready.append(path)
                del self.pending[key]
        return tuple(sorted(ready, key=lambda item: str(item).casefold()))
