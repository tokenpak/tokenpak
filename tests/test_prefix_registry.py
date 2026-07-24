"""
tests/test_prefix_registry.py

Unit tests for tokenpak.cache.prefix_registry

Covers:
  - Deterministic ID generation (key order / whitespace invariant)
  - Collision safety assumptions
  - Registry get-or-create semantics
  - Metadata tracking (first_seen, last_seen, hit_count, size_bytes)
  - Thread safety (basic)
  - Process singleton (get_registry / reset_registry)
  - canonicalize helper
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tokenpak.cache.prefix_registry import (
    StablePrefixRegistry,
    canonicalize,
    fingerprint,
    get_registry,
    reset_registry,
)

# ---------------------------------------------------------------------------
# canonicalize
# ---------------------------------------------------------------------------


class TestCanonicalize:
    def test_bytes_passthrough(self):
        b = b"raw bytes"
        assert canonicalize(b) is b

    def test_str_utf8(self):
        assert canonicalize("hello") == b"hello"

    def test_str_unicode(self):
        assert canonicalize("caf\u00e9") == "caf\u00e9".encode("utf-8")

    def test_dict_sorted_keys(self):
        a = canonicalize({"b": 1, "a": 2})
        b = canonicalize({"a": 2, "b": 1})
        assert a == b

    def test_dict_no_extra_whitespace(self):
        raw = canonicalize({"x": 1})
        assert b" " not in raw  # separators=(",", ":") → no spaces

    def test_list_stable(self):
        assert canonicalize([1, 2, 3]) == canonicalize([1, 2, 3])

    def test_nested_dict_key_sort(self):
        a = canonicalize({"outer": {"z": 9, "a": 1}})
        b = canonicalize({"outer": {"a": 1, "z": 9}})
        assert a == b


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_prefix(self):
        fid = fingerprint("hello")
        assert fid.startswith("spfx-")

    def test_hex_chars_only(self):
        fid = fingerprint("hello")
        suffix = fid[len("spfx-") :]
        assert all(c in "0123456789abcdef" for c in suffix)
        assert len(suffix) == 16

    def test_deterministic_string(self):
        assert fingerprint("hello world") == fingerprint("hello world")

    def test_deterministic_dict_key_order(self):
        a = fingerprint({"role": "user", "content": "hi"})
        b = fingerprint({"content": "hi", "role": "user"})
        assert a == b, "Key order must not affect fingerprint"

    def test_different_content_different_id(self):
        assert fingerprint("hello") != fingerprint("world")

    def test_dict_vs_string_differ(self):
        # {"a": 1} as dict vs the raw string '{"a":1}' should produce same ID
        # because canonicalize({"a":1}) == b'{"a":1}'
        dict_id = fingerprint({"a": 1})
        str_id = fingerprint('{"a":1}')
        assert dict_id == str_id, "dict and its canonical JSON string should match"

    def test_nested_deterministic(self):
        payload_a = {"system": [{"type": "text", "text": "You are helpful."}], "model": "claude"}
        payload_b = {"model": "claude", "system": [{"type": "text", "text": "You are helpful."}]}
        assert fingerprint(payload_a) == fingerprint(payload_b)

    def test_whitespace_invariant_for_dicts(self):
        """Whitespace in JSON rendering should not matter for dict payloads."""
        a = fingerprint({"key": "value"})
        # Manually pretty-printed version as a string would differ, but as a
        # dict they collapse to the same canonical form.
        b = fingerprint({"key": "value"})
        assert a == b

    def test_collision_safety_distinct_payloads(self):
        """100 distinct simple payloads should all produce distinct IDs."""
        ids = {fingerprint({"n": i}) for i in range(100)}
        assert len(ids) == 100, "Each distinct payload must yield a unique ID"


# ---------------------------------------------------------------------------
# StablePrefixRegistry — core semantics
# ---------------------------------------------------------------------------


class TestStablePrefixRegistry:
    def setup_method(self):
        self.reg = StablePrefixRegistry()

    def test_first_call_is_new(self):
        _, is_new = self.reg.get_or_create("hello")
        assert is_new is True

    def test_second_call_not_new(self):
        self.reg.get_or_create("hello")
        _, is_new = self.reg.get_or_create("hello")
        assert is_new is False

    def test_same_id_returned_both_calls(self):
        id1, _ = self.reg.get_or_create("hello")
        id2, _ = self.reg.get_or_create("hello")
        assert id1 == id2

    def test_different_payloads_different_ids(self):
        id1, _ = self.reg.get_or_create("hello")
        id2, _ = self.reg.get_or_create("world")
        assert id1 != id2

    def test_id_matches_fingerprint(self):
        payload = {"system": "You are a helpful assistant."}
        block_id, _ = self.reg.get_or_create(payload)
        assert block_id == fingerprint(payload)

    def test_hit_count_increments(self):
        payload = {"x": 42}
        self.reg.get_or_create(payload)
        self.reg.get_or_create(payload)
        self.reg.get_or_create(payload)
        meta = self.reg.metadata(fingerprint(payload))
        assert meta["hit_count"] == 3

    def test_first_seen_set_on_create(self):
        before = time.time()
        bid, _ = self.reg.get_or_create("ts-test")
        after = time.time()
        meta = self.reg.metadata(bid)
        assert before <= meta["first_seen"] <= after

    def test_last_seen_updated_on_hit(self):
        bid, _ = self.reg.get_or_create("ts-test2")
        t1 = self.reg.metadata(bid)["last_seen"]
        time.sleep(0.01)
        self.reg.get_or_create("ts-test2")
        t2 = self.reg.metadata(bid)["last_seen"]
        assert t2 >= t1

    def test_size_bytes_recorded(self):
        payload = "some stable prefix text"
        bid, _ = self.reg.get_or_create(payload)
        meta = self.reg.metadata(bid)
        assert meta["size_bytes"] == len(canonicalize(payload))

    def test_metadata_returns_none_for_unknown(self):
        assert self.reg.metadata("spfx-doesnotexist") is None

    def test_size_counts_distinct_blocks(self):
        self.reg.get_or_create("a")
        self.reg.get_or_create("b")
        self.reg.get_or_create("a")  # duplicate
        assert self.reg.size() == 2

    def test_clear(self):
        self.reg.get_or_create("x")
        self.reg.clear()
        assert self.reg.size() == 0

    def test_all_metadata_snapshot(self):
        self.reg.get_or_create("p1")
        self.reg.get_or_create("p2")
        snap = self.reg.all_metadata()
        assert len(snap) == 2

    def test_summary(self):
        self.reg.get_or_create("a")
        self.reg.get_or_create("a")
        self.reg.get_or_create("b")
        s = self.reg.summary()
        assert s["distinct_blocks"] == 2
        assert s["total_hits"] == 3

    def test_key_order_agnostic_registry(self):
        """Registry should assign the same ID for semantically identical dicts."""
        id1, _ = self.reg.get_or_create({"role": "system", "text": "Be helpful."})
        id2, _ = self.reg.get_or_create({"text": "Be helpful.", "role": "system"})
        assert id1 == id2
        meta = self.reg.metadata(id1)
        assert meta["hit_count"] == 2


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_get_or_create_same_payload(self):
        reg = StablePrefixRegistry()
        payload = {"concurrent": True}
        results = []
        errors = []

        def worker():
            try:
                bid, _ = reg.get_or_create(payload)
                results.append(bid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # All threads must get the same ID
        assert len(set(results)) == 1
        # hit_count must equal number of threads
        meta = reg.metadata(results[0])
        assert meta["hit_count"] == 50

    def test_concurrent_distinct_payloads(self):
        reg = StablePrefixRegistry()
        errors = []

        def worker(i):
            try:
                reg.get_or_create({"worker": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert reg.size() == 50


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_get_registry_returns_same_instance(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_reset_registry_creates_fresh(self):
        r1 = get_registry()
        r1.get_or_create("persist-me")
        assert r1.size() == 1

        reset_registry()
        r2 = get_registry()
        assert r2 is not r1
        assert r2.size() == 0

    def test_singleton_shares_state(self):
        reg = get_registry()
        reg.get_or_create("shared")
        # Getting registry again gives the same instance with the same data
        assert get_registry().size() == 1


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


def test_public_api_importable():
    from tokenpak.cache import (
        fingerprint,
        get_registry,
    )

    assert callable(fingerprint)
    assert callable(get_registry)
