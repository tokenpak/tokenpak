"""Help<->parser parity regression test (DISPATCH-P0-4-CLI-HELP-REGISTRY-PARITY).

Guards the two honesty invariants behind the CLI help catalog:

1. **No phantom commands.** Every command advertised in the help registry
   (``commands.json``) is actually invokable by the parser. A user must never
   see a command in ``tokenpak help --all`` that returns ``Unknown command`` on
   invoke.
2. **No silent drift.** The number of invokable-but-unadvertised commands is
   pinned to a reviewed snapshot. A new invokable command that is neither
   cataloged in the registry nor a deliberate internal verb trips this test,
   forcing a conscious decision rather than silent re-drift.

Both checks derive the "invokable" set live from the argparse parser via
:func:`tokenpak._cli_core.registered_command_names`, so the advertised count can
never drift from what the CLI actually dispatches (``feedback_always_dynamic``).
"""

import json
from pathlib import Path

from tokenpak._cli_core import build_parser, registered_command_names

REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent
    / "tokenpak" / "core" / "registry" / "commands.json"
)

# The nine commands brought into the help catalog by DISPATCH-P0-4: ``pak`` plus
# its verb family, and the eight previously-invisible-but-invokable commands.
P0_4_ADDED = {
    "pak", "pakplan", "tip", "creds", "claude", "codex", "prove", "init", "setup",
}

# The eight phantom commands disposed by DISPATCH-P0-4 (default = REMOVE; none
# confirmed against the Pro catalog, none with near-term OSS roadmap evidence).
# They must never reappear as advertised commands without becoming invokable.
P0_4_DISPOSED = {
    "workflow", "handoff", "retain", "metrics",
    "policy", "sla", "compression", "maintenance",
}

# Snapshot (as of DISPATCH-P0-4, 2026-06-11) of how many invokable verbs are
# intentionally kept OUT of the public help catalog: internal / dev /
# experimental verbs and commands pending a separate cataloging decision. These
# are NOT phantoms -- they all dispatch. The snapshot guards against a *new*
# invokable command silently appearing unadvertised: if you add a user-facing
# command, catalog it in commands.json so help advertises it; if you add a
# deliberately-internal verb, bump this number in the same change so the
# decision is explicit and reviewed.
EXPECTED_INTERNAL_UNADVERTISED = 34  # public OSS surface: two internal verbs present in other distributions are not registered here


def _registry_commands():
    data = json.loads(REGISTRY_PATH.read_text())
    return [c["command"] for c in data.get("commands", [])]


def test_no_phantom_commands():
    """Every advertised command must be invokable (no ``Unknown command``)."""
    invokable = registered_command_names(build_parser())
    advertised = set(_registry_commands())
    phantoms = sorted(advertised - invokable)
    assert not phantoms, (
        "Help registry advertises command(s) the parser will not accept "
        f"(invoking them returns 'Unknown command'): {phantoms}"
    )


def test_added_commands_are_advertised_and_invokable():
    """The P0-4 additions surface in help AND dispatch."""
    invokable = registered_command_names(build_parser())
    advertised = set(_registry_commands())
    assert P0_4_ADDED <= advertised, (
        f"missing from help registry: {sorted(P0_4_ADDED - advertised)}"
    )
    assert P0_4_ADDED <= invokable, (
        f"not invokable by parser: {sorted(P0_4_ADDED - invokable)}"
    )


def test_disposed_phantoms_absent():
    """The eight disposed phantoms must not reappear in the registry."""
    advertised = set(_registry_commands())
    leaked = sorted(advertised & P0_4_DISPOSED)
    assert not leaked, f"disposed phantom command(s) back in registry: {leaked}"


def test_no_silent_invisible_drift():
    """The count of invokable-but-unadvertised verbs matches the snapshot."""
    invokable = registered_command_names(build_parser())
    advertised = set(_registry_commands())
    unadvertised = invokable - advertised
    assert len(unadvertised) == EXPECTED_INTERNAL_UNADVERTISED, (
        "The number of invokable-but-unadvertised commands changed "
        f"({len(unadvertised)} vs expected {EXPECTED_INTERNAL_UNADVERTISED}). "
        "If you added a user-facing command, add it to commands.json so help "
        "advertises it; if you added a deliberately-internal verb, bump "
        "EXPECTED_INTERNAL_UNADVERTISED in the same change."
    )


def test_advertised_count_is_derived_not_hardcoded():
    """The count help advertises is derived from the live registry, and honest.

    ``help`` renders ``len(_load_registry())``; this asserts that derivation
    matches the registry file and -- combined with ``test_no_phantom_commands``
    -- that every counted command is genuinely invokable.
    """
    from tokenpak.cli.commands.help import _load_registry

    assert len(_load_registry()) == len(_registry_commands())
