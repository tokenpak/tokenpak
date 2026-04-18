---
title: Module Template
type: template
---

# Module Template

Use this layout for a new Python subpackage under `tokenpak/`. Follows `01-architecture-standard.md §3`.

## File layout

```
tokenpak/<subsystem>/
  __init__.py          # re-exports the public surface; nothing else
  <concept>.py         # one concept per file; module name matches the concept
  _internal.py         # leading underscore = not stable API
  errors.py            # subclass of Exception for this subsystem; specialize from there
  tests/
    __init__.py
    test_<concept>.py
  README.md            # optional; required if the subsystem has non-obvious invariants
```

## `__init__.py` (canonical shape)

```python
"""
<Subsystem name>.

<One paragraph: what this subsystem owns, and what it does not own.>
<One paragraph: any invariant or hazard callers should know.>
"""

from __future__ import annotations

from tokenpak.<subsystem>._internal import _private_helper_not_exported  # noqa: F401
from tokenpak.<subsystem>.<concept> import <PublicClass>, <public_function>
from tokenpak.<subsystem>.errors import <SubsystemError>

__all__ = [
    "<PublicClass>",
    "<public_function>",
    "<SubsystemError>",
]
```

## `<concept>.py` (canonical shape)

```python
"""
<Concept name> — <one-sentence description>.

<Paragraph describing what this module owns. Reference the Glossary for any domain terms.>
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from tokenpak.core import <core_types>
from tokenpak.<subsystem>.errors import <SubsystemError>

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class <PublicClass>:
    """<One-line summary.>

    <Paragraph describing the class's role. Mention thread-safety, mutation
    rules, and any invariants. Glossary terms must match 08-naming-glossary.md.>
    """

    field_one: str
    field_two: int

    def <public_method>(self, arg: str) -> <ReturnType>:
        """<One-line summary.>

        Args:
            arg: <one-line description>.

        Returns:
            <one-line description>.

        Raises:
            <SubsystemError>: <when and why>.
        """
        logger.debug("<method>: <what-happened>", extra={"field_one": self.field_one})
        ...


def <public_function>(<params>) -> <ReturnType>:
    """<One-line summary.>

    <Paragraph if needed. Skip if the one-liner is enough.>
    """
    ...
```

## `errors.py`

```python
"""<Subsystem> exceptions."""

from __future__ import annotations


class <SubsystemError>(Exception):
    """Base exception for the <subsystem> subsystem."""

    exit_code: int = 1


class <SpecificError>(<SubsystemError>):
    """Raised when <specific condition>."""
```

## `tests/test_<concept>.py`

```python
"""Tests for tokenpak.<subsystem>.<concept>."""

from __future__ import annotations

import pytest

from tokenpak.<subsystem> import <PublicClass>, <public_function>
from tokenpak.<subsystem>.errors import <SubsystemError>


def test_<happy_path_behavior>():
    result = <public_function>(<good_args>)
    assert result == <expected>


def test_<edge_case_behavior>():
    with pytest.raises(<SubsystemError>):
        <public_function>(<bad_args>)
```

## README.md (optional, include when the subsystem has non-obvious rules)

```markdown
---
title: <Subsystem name>
type: subsystem-readme
---

# <Subsystem name>

<One-paragraph summary. What it owns, what it doesn't.>

## Invariants

- <Invariant one. E.g., "Exactly one refresh task per provider at any time.">
- <Invariant two.>

## Entry points

- `<PublicClass>` — <when to use it>.
- `<public_function>` — <when to use it>.

## Non-goals

- <What this subsystem explicitly does not do, with the alternative subsystem to use instead.>

## Hazards

- <Things that break if you touch them wrong. Tie each to a test that protects against regression.>
```

## Checklist before merging a new subsystem

- [ ] Directory listed in `01-architecture-standard.md §1` table.
- [ ] Fits the Level 0–3 hierarchy.
- [ ] `__init__.py` only re-exports; no logic.
- [ ] All public names have type hints.
- [ ] Module docstrings present.
- [ ] Tests exist for happy path + at least one edge case.
- [ ] Errors teach (`02-code-standard.md §3`).
- [ ] No hardcoded enumerations (`01-architecture-standard.md §4`).
- [ ] Logger named `__name__`.
- [ ] Any new Glossary terms added to `08-naming-glossary.md` in the same PR.
