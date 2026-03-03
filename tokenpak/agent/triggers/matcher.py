"""Event pattern matching for TokenPak triggers."""

from __future__ import annotations

import fnmatch
import re
from typing import Optional


def match_event(pattern: str, event: str) -> bool:
    """Return True if *event* matches *pattern*.

    Supported pattern forms:
      file:changed:<glob>   e.g.  file:changed:*.py
      file:created:<glob>   e.g.  file:created:/tmp/watch/*
      cost:daily><amount>   e.g.  cost:daily>5.00
      timer:<interval>      e.g.  timer:5m  (matched by daemon)
    """
    if pattern == event:
        return True

    # file events
    for kind in ("file:changed:", "file:created:"):
        if pattern.startswith(kind) and event.startswith(kind):
            glob = pattern[len(kind):]
            path = event[len(kind):]
            # match on full path OR basename
            return fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(
                path.split("/")[-1], glob
            )

    # cost threshold  cost:daily>10
    m = re.match(r"cost:daily>(\d+(?:\.\d+)?)", pattern)
    if m:
        threshold = float(m.group(1))
        em = re.match(r"cost:daily>(\d+(?:\.\d+)?)", event)
        if em:
            return float(em.group(1)) >= threshold

    # timer patterns are matched by daemon (not here)
    return False
