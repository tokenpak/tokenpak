"""Event pattern matching for TokenPak triggers."""

from __future__ import annotations

import fnmatch
import re


def match_event(pattern: str, event: str) -> bool:
    """Return True if *event* matches *pattern*.

    Supported pattern forms:
      file:changed:<glob>       e.g.  file:changed:*.py
      file:created:<glob>       e.g.  file:created:/tmp/watch/*
      cost:daily><amount>       e.g.  cost:daily>5.00
      timer:<interval>          e.g.  timer:5m  (matched by daemon)
      git:push                  fires on git post-push
      git:commit                fires on git post-commit
      agent:finished            fires when any agent task finishes
      agent:finished:<name>     fires when a specific agent/task finishes
      agent:failed              fires when any agent task fails
      agent:failed:<name>       fires when a specific agent/task fails
    """
    if pattern == event:
        return True

    # file events
    for kind in ("file:changed:", "file:created:"):
        if pattern.startswith(kind) and event.startswith(kind):
            glob = pattern[len(kind) :]
            path = event[len(kind) :]
            # match on full path OR basename
            return fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path.split("/")[-1], glob)

    # cost threshold  cost:daily>10
    m = re.match(r"cost:daily>(\d+(?:\.\d+)?)", pattern)
    if m:
        threshold = float(m.group(1))
        em = re.match(r"cost:daily>(\d+(?:\.\d+)?)", event)
        if em:
            return float(em.group(1)) >= threshold

    # agent events: agent:finished or agent:finished:<name>
    for kind in ("agent:finished", "agent:failed"):
        if pattern == kind and event.startswith(kind):
            return True
        if pattern.startswith(kind + ":") and event.startswith(kind + ":"):
            name_pat = pattern[len(kind) + 1 :]
            name_evt = event[len(kind) + 1 :]
            return fnmatch.fnmatch(name_evt, name_pat)

    # git events: allow git:push or git:commit to match git:push:<branch> etc.
    for kind in ("git:push", "git:commit"):
        if pattern == kind and (event == kind or event.startswith(kind + ":")):
            return True

    # timer patterns are matched by daemon (not here)
    return False
