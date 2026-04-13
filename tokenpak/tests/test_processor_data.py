"""Unit tests for processors/data.py — DataProcessor."""
import json

import pytest

from tokenpak.processors.data import DataProcessor


class TestDataProcessorInit:
    def test_instantiation(self):
        proc = DataProcessor()
        assert proc is not None

    def test_has_process_method(self):
        assert callable(DataProcessor().process)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


class TestDataProcessorJSON:
    def setup_method(self):
        self.proc = DataProcessor()

    def test_valid_json_object(self):
        data = json.dumps({"name": "Alice", "age": 30})
        result = self.proc.process(data, path="data.json")
        assert "[JSON Schema]" in result
        assert "name" in result

    def test_valid_json_array(self):
        data = json.dumps([{"id": i} for i in range(3)])
        result = self.proc.process(data, path="data.json")
        assert "[JSON Schema]" in result
        assert "[Array: 3 items]" in result

    def test_invalid_json(self):
        result = self.proc.process("{bad json{{", path="data.json")
        assert "[Invalid JSON" in result

    def test_empty_json_array(self):
        data = json.dumps([])
        result = self.proc.process(data, path="data.json")
        assert result is not None

    def test_json_nested_types(self):
        data = json.dumps({"s": "str", "n": 1, "f": 1.5, "b": True, "null": None})
        result = self.proc.process(data, path="data.json")
        assert "string" in result
        assert "integer" in result
        assert "boolean" in result
        assert "null" in result

    def test_json_array_shows_sample(self):
        data = json.dumps([{"id": i, "value": i * 2} for i in range(5)])
        result = self.proc.process(data, path="data.json")
        assert "[Sample (first item)]" in result


# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------


class TestDataProcessorCSV:
    def setup_method(self):
        self.proc = DataProcessor()

    def test_csv_basic(self):
        content = "name,age,city\nAlice,30,NYC\nBob,25,LA\n"
        result = self.proc.process(content, path="data.csv")
        assert "[CSV:" in result
        assert "name" in result
        assert "age" in result

    def test_csv_empty(self):
        result = self.proc.process("", path="data.csv")
        assert "[Empty CSV]" in result

    def test_tsv_basic(self):
        content = "name\tage\tcity\nAlice\t30\tNYC\n"
        result = self.proc.process(content, path="data.tsv")
        assert "[CSV:" in result
        assert "name" in result

    def test_csv_single_row_header_only(self):
        content = "col1,col2,col3\n"
        result = self.proc.process(content, path="file.csv")
        assert "[CSV:" in result
        assert "col1" in result

    def test_csv_column_count_shown(self):
        content = "a,b,c,d\n1,2,3,4\n"
        result = self.proc.process(content, path="data.csv")
        assert "4 columns" in result


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------


class TestDataProcessorYAML:
    def setup_method(self):
        self.proc = DataProcessor()

    def test_valid_yaml_dict(self):
        content = "name: Alice\nage: 30\nactive: true\n"
        result = self.proc.process(content, path="config.yaml")
        assert "[YAML" in result

    def test_valid_yaml_yml_extension(self):
        content = "key: value\n"
        result = self.proc.process(content, path="settings.yml")
        assert result is not None
        assert len(result) > 0

    def test_yaml_fallback_on_invalid(self):
        # Key with unclosed bracket triggers parse error
        content = "key: [unclosed\n"
        result = self.proc.process(content, path="bad.yaml")
        assert result is not None
        assert len(result) > 0

    def test_yaml_empty_string(self):
        result = self.proc.process("", path="empty.yaml")
        assert result is not None


# ---------------------------------------------------------------------------
# TOML
# ---------------------------------------------------------------------------


class TestDataProcessorTOML:
    def setup_method(self):
        self.proc = DataProcessor()

    def test_valid_toml(self):
        content = '[tool]\nname = "tokenpak"\nversion = "1.0"\n'
        result = self.proc.process(content, path="pyproject.toml")
        # Either parsed schema or fallback to first 50 lines — both acceptable
        assert result is not None
        assert len(result) > 0

    def test_invalid_toml_fallback(self):
        content = "bad toml content ====\n"
        result = self.proc.process(content, path="bad.toml")
        assert result is not None


# ---------------------------------------------------------------------------
# Unknown extensions
# ---------------------------------------------------------------------------


class TestDataProcessorUnknown:
    def setup_method(self):
        self.proc = DataProcessor()

    def test_unknown_extension_returns_first_1000_chars(self):
        content = "x" * 2000
        result = self.proc.process(content, path="file.xyz")
        assert len(result) == 1000

    def test_unknown_extension_short_content(self):
        content = "short content"
        result = self.proc.process(content, path="file.xyz")
        assert result == content

    def test_no_path_returns_first_1000(self):
        content = "a" * 500
        result = self.proc.process(content)
        assert result == content


# ---------------------------------------------------------------------------
# _extract_json_schema
# ---------------------------------------------------------------------------


class TestExtractJsonSchema:
    def setup_method(self):
        self.proc = DataProcessor()

    def test_string_type(self):
        schema = self.proc._extract_json_schema("hello")
        assert schema == "string"

    def test_integer_type(self):
        assert self.proc._extract_json_schema(42) == "integer"

    def test_float_type(self):
        assert self.proc._extract_json_schema(3.14) == "number"

    def test_bool_type(self):
        assert self.proc._extract_json_schema(True) == "boolean"

    def test_none_type(self):
        assert self.proc._extract_json_schema(None) == "null"

    def test_dict_maps_keys(self):
        data = {"name": "Alice", "age": 30}
        schema = self.proc._extract_json_schema(data)
        assert schema["name"] == "string"
        assert schema["age"] == "integer"

    def test_list_wraps_first_element(self):
        schema = self.proc._extract_json_schema([1, 2, 3])
        assert isinstance(schema, list)
        assert schema[0] == "integer"

    def test_empty_list(self):
        schema = self.proc._extract_json_schema([])
        assert schema == "[]"

    def test_depth_limit(self):
        nested = {"a": {"b": {"c": {"d": "deep"}}}}
        schema = self.proc._extract_json_schema(nested, depth=0, max_depth=2)
        # At max_depth=2, the third level returns a type placeholder
        assert schema["a"]["b"] == "<dict>"

    def test_large_dict_truncated_at_15_keys(self):
        data = {f"key{i}": i for i in range(20)}
        schema = self.proc._extract_json_schema(data)
        assert "..." in schema
        assert len(schema) == 16  # 15 keys + "..."
