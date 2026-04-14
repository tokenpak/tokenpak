"""
Unit tests for tokenpak.proxy.middleware.audit_trail

Covers: enums, dataclasses, factory functions, to_dict/to_json serialisation.
"""

import json
import re
import unittest

from tokenpak.proxy.middleware.audit_trail import (
    BlockAudit,
    BlockType,
    CacheAudit,
    CompileAudit,
    CompressionMethod,
    MetricsAudit,
    _get_iso_timestamp,
    create_cache_audit,
    create_compile_audit,
    create_metrics_audit,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestCompressionMethodEnum(unittest.TestCase):
    def test_values(self):
        self.assertEqual(CompressionMethod.EXTRACTIVE.value, "extractive")
        self.assertEqual(CompressionMethod.LLM.value, "llm")
        self.assertEqual(CompressionMethod.TRUNCATION.value, "truncation")
        self.assertEqual(CompressionMethod.DEDUPLICATION.value, "deduplication")
        self.assertEqual(CompressionMethod.SEMANTIC.value, "semantic")

    def test_is_string_enum(self):
        self.assertIsInstance(CompressionMethod.EXTRACTIVE, str)


class TestBlockTypeEnum(unittest.TestCase):
    def test_values(self):
        self.assertEqual(BlockType.INSTRUCTION.value, "instruction")
        self.assertEqual(BlockType.KNOWLEDGE.value, "knowledge")
        self.assertEqual(BlockType.EVIDENCE.value, "evidence")
        self.assertEqual(BlockType.EXAMPLE.value, "example")
        self.assertEqual(BlockType.CUSTOM.value, "custom")

    def test_is_string_enum(self):
        self.assertIsInstance(BlockType.INSTRUCTION, str)


# ---------------------------------------------------------------------------
# _get_iso_timestamp
# ---------------------------------------------------------------------------


class TestGetIsoTimestamp(unittest.TestCase):
    _ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

    def test_ends_with_z(self):
        ts = _get_iso_timestamp()
        self.assertTrue(ts.endswith("Z"), f"Expected Z suffix: {ts}")

    def test_matches_iso8601_pattern(self):
        ts = _get_iso_timestamp()
        self.assertRegex(ts, self._ISO_Z_RE)


# ---------------------------------------------------------------------------
# BlockAudit
# ---------------------------------------------------------------------------


class TestBlockAudit(unittest.TestCase):
    def test_full_initialization(self):
        audit = BlockAudit(
            block_id="b-001",
            block_type=BlockType.INSTRUCTION,
            original_size=200,
            final_size=120,
            action="compacted",
            compression_method=CompressionMethod.EXTRACTIVE,
            reason="Redundant preamble",
            similarity_to_kept=0.95,
        )
        self.assertEqual(audit.block_id, "b-001")
        self.assertEqual(audit.block_type, BlockType.INSTRUCTION)
        self.assertEqual(audit.original_size, 200)
        self.assertEqual(audit.final_size, 120)
        self.assertEqual(audit.action, "compacted")
        self.assertEqual(audit.compression_method, CompressionMethod.EXTRACTIVE)
        self.assertEqual(audit.reason, "Redundant preamble")
        self.assertAlmostEqual(audit.similarity_to_kept, 0.95)

    def test_optional_fields_default_to_none_or_empty(self):
        audit = BlockAudit(
            block_id="b-002",
            block_type=BlockType.CUSTOM,
            original_size=50,
            final_size=0,
            action="removed",
        )
        self.assertIsNone(audit.compression_method)
        self.assertEqual(audit.reason, "")
        self.assertIsNone(audit.similarity_to_kept)


# ---------------------------------------------------------------------------
# CompileAudit
# ---------------------------------------------------------------------------


def _make_compile_audit(**overrides) -> CompileAudit:
    defaults = dict(
        request_id="req-001",
        timestamp="2026-04-12T12:00:00Z",
        input_block_count=3,
        input_blocks_by_type={BlockType.INSTRUCTION: 2, BlockType.KNOWLEDGE: 1},
        input_total_size=1500,
        output_block_count=2,
        output_blocks_by_type={BlockType.INSTRUCTION: 1, BlockType.KNOWLEDGE: 1},
        output_total_size=1000,
    )
    defaults.update(overrides)
    return CompileAudit(**defaults)


class TestCompileAuditDefaults(unittest.TestCase):
    def test_default_fields(self):
        audit = _make_compile_audit()
        self.assertEqual(audit.blocks_audited, [])
        self.assertEqual(audit.compression_methods_used, {})
        self.assertEqual(audit.parse_latency_ms, 0.0)
        self.assertEqual(audit.compile_latency_ms, 0.0)
        self.assertEqual(audit.render_latency_ms, 0.0)
        self.assertEqual(audit.total_latency_ms, 0.0)
        self.assertEqual(audit.compression_ratio, 1.0)
        self.assertEqual(audit.tokens_removed, 0)
        self.assertEqual(audit.errors, [])


class TestCompileAuditToDict(unittest.TestCase):
    def test_input_blocks_enum_keys_become_strings(self):
        audit = _make_compile_audit()
        d = audit.to_dict()
        self.assertIn("instruction", d["input_blocks_by_type"])
        self.assertIn("knowledge", d["input_blocks_by_type"])

    def test_output_blocks_enum_keys_become_strings(self):
        audit = _make_compile_audit()
        d = audit.to_dict()
        self.assertIn("instruction", d["output_blocks_by_type"])

    def test_compression_methods_enum_keys_become_strings(self):
        audit = _make_compile_audit(
            compression_methods_used={
                CompressionMethod.LLM: 2,
                CompressionMethod.TRUNCATION: 1,
            }
        )
        d = audit.to_dict()
        self.assertIn("llm", d["compression_methods_used"])
        self.assertIn("truncation", d["compression_methods_used"])

    def test_blocks_audited_enum_fields_become_strings(self):
        block = BlockAudit(
            block_id="b-1",
            block_type=BlockType.KNOWLEDGE,
            original_size=100,
            final_size=80,
            action="compacted",
            compression_method=CompressionMethod.EXTRACTIVE,
        )
        audit = _make_compile_audit(blocks_audited=[block])
        d = audit.to_dict()
        self.assertEqual(d["blocks_audited"][0]["block_type"], "knowledge")
        self.assertEqual(d["blocks_audited"][0]["compression_method"], "extractive")

    def test_blocks_audited_none_compression_method(self):
        block = BlockAudit(
            block_id="b-2",
            block_type=BlockType.EVIDENCE,
            original_size=50,
            final_size=50,
            action="kept",
        )
        audit = _make_compile_audit(blocks_audited=[block])
        d = audit.to_dict()
        self.assertIsNone(d["blocks_audited"][0]["compression_method"])

    def test_request_id_preserved(self):
        audit = _make_compile_audit(request_id="req-xyz")
        d = audit.to_dict()
        self.assertEqual(d["request_id"], "req-xyz")


class TestCompileAuditToJson(unittest.TestCase):
    def test_is_valid_json(self):
        audit = _make_compile_audit()
        j = audit.to_json()
        parsed = json.loads(j)
        self.assertIsInstance(parsed, dict)

    def test_contains_request_id(self):
        audit = _make_compile_audit(request_id="req-json-01")
        parsed = json.loads(audit.to_json())
        self.assertEqual(parsed["request_id"], "req-json-01")

    def test_enum_keys_are_strings_in_json(self):
        audit = _make_compile_audit()
        parsed = json.loads(audit.to_json())
        # Keys in input_blocks_by_type should be string values, not enum names
        self.assertIn("instruction", parsed["input_blocks_by_type"])


# ---------------------------------------------------------------------------
# CacheAudit
# ---------------------------------------------------------------------------


class TestCacheAudit(unittest.TestCase):
    def test_default_fields(self):
        audit = CacheAudit(
            request_id="req-c01",
            timestamp="2026-04-12T12:00:00Z",
            operation="get",
        )
        self.assertFalse(audit.cache_hit)
        self.assertEqual(audit.cached_value_size, 0)
        self.assertIsNone(audit.block_id)
        self.assertIsNone(audit.ttl_seconds)
        self.assertEqual(audit.message, "")

    def test_full_initialization(self):
        audit = CacheAudit(
            request_id="req-c02",
            timestamp="2026-04-12T12:00:00Z",
            operation="set",
            block_id="b-456",
            cache_hit=True,
            cached_value_size=1024,
            ttl_seconds=300,
            message="stored",
        )
        self.assertEqual(audit.operation, "set")
        self.assertEqual(audit.block_id, "b-456")
        self.assertTrue(audit.cache_hit)
        self.assertEqual(audit.cached_value_size, 1024)
        self.assertEqual(audit.ttl_seconds, 300)


# ---------------------------------------------------------------------------
# MetricsAudit
# ---------------------------------------------------------------------------


class TestMetricsAudit(unittest.TestCase):
    def test_default_fields(self):
        audit = MetricsAudit(
            request_id="req-m01",
            timestamp="2026-04-12T12:00:00Z",
            aggregation_window="1h",
        )
        self.assertEqual(audit.data_points_returned, 0)
        self.assertEqual(audit.metrics_included, [])

    def test_full_initialization(self):
        audit = MetricsAudit(
            request_id="req-m02",
            timestamp="2026-04-12T12:00:00Z",
            aggregation_window="24h",
            data_points_returned=48,
            metrics_included=["latency_ms", "tokens_used", "error_rate"],
        )
        self.assertEqual(audit.aggregation_window, "24h")
        self.assertEqual(audit.data_points_returned, 48)
        self.assertEqual(len(audit.metrics_included), 3)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


class TestCreateCompileAudit(unittest.TestCase):
    def test_sets_request_id(self):
        audit = create_compile_audit(
            request_id="fac-001",
            input_block_count=4,
            input_blocks_by_type={BlockType.INSTRUCTION: 4},
            input_total_size=800,
        )
        self.assertEqual(audit.request_id, "fac-001")

    def test_sets_input_fields(self):
        audit = create_compile_audit(
            request_id="fac-002",
            input_block_count=5,
            input_blocks_by_type={BlockType.KNOWLEDGE: 5},
            input_total_size=2000,
        )
        self.assertEqual(audit.input_block_count, 5)
        self.assertEqual(audit.input_total_size, 2000)

    def test_output_fields_initialised_to_zero(self):
        audit = create_compile_audit(
            request_id="fac-003",
            input_block_count=2,
            input_blocks_by_type={BlockType.EXAMPLE: 2},
            input_total_size=500,
        )
        self.assertEqual(audit.output_block_count, 0)
        self.assertEqual(audit.output_total_size, 0)
        self.assertEqual(audit.output_blocks_by_type, {})

    def test_timestamp_is_utc_z_format(self):
        audit = create_compile_audit(
            request_id="fac-004",
            input_block_count=1,
            input_blocks_by_type={BlockType.CUSTOM: 1},
            input_total_size=100,
        )
        self.assertTrue(audit.timestamp.endswith("Z"))


class TestCreateCacheAudit(unittest.TestCase):
    def test_sets_operation_and_block_id(self):
        audit = create_cache_audit("fac-c01", "get", "b-001")
        self.assertEqual(audit.operation, "get")
        self.assertEqual(audit.block_id, "b-001")

    def test_no_block_id(self):
        audit = create_cache_audit("fac-c02", "clear")
        self.assertIsNone(audit.block_id)

    def test_timestamp_is_utc_z_format(self):
        audit = create_cache_audit("fac-c03", "set")
        self.assertTrue(audit.timestamp.endswith("Z"))

    def test_all_operations(self):
        for op in ("get", "set", "invalidate", "clear"):
            audit = create_cache_audit("req", op)
            self.assertEqual(audit.operation, op)


class TestCreateMetricsAudit(unittest.TestCase):
    def test_sets_aggregation_window(self):
        audit = create_metrics_audit("fac-m01", "6h")
        self.assertEqual(audit.aggregation_window, "6h")

    def test_timestamp_is_utc_z_format(self):
        audit = create_metrics_audit("fac-m02", "24h")
        self.assertTrue(audit.timestamp.endswith("Z"))


if __name__ == "__main__":
    unittest.main()
