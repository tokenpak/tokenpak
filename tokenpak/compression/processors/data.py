"""Data processor for JSON, CSV, YAML, and TOML files."""

import csv
import io
import json
from pathlib import Path


class DataProcessor:
    """Extract schema and samples from structured data files."""

    def process(self, content: str, path: str = "") -> str:
        """Process structured data files into schema + sample."""
        ext = Path(path).suffix.lower()

        if ext == ".json":
            return self._process_json(content)
        elif ext in (".csv", ".tsv"):
            return self._process_csv(content, delimiter="\t" if ext == ".tsv" else ",")
        elif ext in (".yaml", ".yml"):
            return self._process_yaml(content)
        elif ext == ".toml":
            return self._process_toml(content)
        else:
            return content[:1000]

    def _process_json(self, content: str) -> str:
        """Extract JSON schema with types and sample values."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return f"[Invalid JSON — {len(content)} chars]"

        schema = self._extract_json_schema(data, depth=0, max_depth=3)
        result = ["[JSON Schema]", json.dumps(schema, indent=2)]

        # Add sample for arrays
        if isinstance(data, list):
            result.append(f"\n[Array: {len(data)} items]")
            if len(data) > 0:
                result.append("[Sample (first item)]")
                sample = json.dumps(data[0], indent=2)
                if len(sample) > 500:
                    sample = sample[:500] + "…"
                result.append(sample)

        return "\n".join(result)

    def _extract_json_schema(self, data, depth: int = 0, max_depth: int = 3) -> dict | str:
        """Recursively extract JSON schema (keys + types)."""
        if depth >= max_depth:
            return f"<{type(data).__name__}>"

        if isinstance(data, dict):
            schema = {}
            for i, (key, value) in enumerate(data.items()):
                if i >= 15:  # Limit keys shown
                    schema["..."] = f"({len(data) - 15} more keys)"
                    break
                schema[key] = self._extract_json_schema(value, depth + 1, max_depth)  # type: ignore[assignment]
            return schema
        elif isinstance(data, list):
            if len(data) == 0:
                return "[]"
            return [self._extract_json_schema(data[0], depth + 1, max_depth)]  # type: ignore[return-value]
        elif isinstance(data, str):
            return "string"
        elif isinstance(data, bool):
            return "boolean"
        elif isinstance(data, int):
            return "integer"
        elif isinstance(data, float):
            return "number"
        elif data is None:
            return "null"
        else:
            return f"<{type(data).__name__}>"

    def _process_csv(self, content: str, delimiter: str = ",") -> str:
        """Extract CSV schema and first 5 rows."""
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        rows = []
        try:
            for i, row in enumerate(reader):
                rows.append(row)
                if i >= 5:  # Header + 5 data rows
                    break
        except csv.Error:
            return f"[CSV parse error — {len(content)} chars]"

        if not rows:
            return "[Empty CSV]"

        # Count total rows
        total_lines = content.count("\n")

        header = rows[0]
        result = [
            f"[CSV: {total_lines} rows, {len(header)} columns]",
            f"[Columns: {', '.join(header)}]",
            "",
            "[Sample (first 5 rows)]",
        ]

        # Format as table
        for row in rows[:6]:
            result.append(delimiter.join(row))

        return "\n".join(result)

    def _process_yaml(self, content: str) -> str:
        """Process YAML — try to parse, fallback to first 50 lines."""
        try:
            import yaml

            data = yaml.safe_load(content)
            if isinstance(data, (dict, list)):
                schema = self._extract_json_schema(data, depth=0, max_depth=3)
                return "[YAML Schema]\n" + json.dumps(schema, indent=2)
        except Exception:
            pass
        # Fallback: first 50 lines
        lines = content.split("\n")[:50]
        return "[YAML — first 50 lines]\n" + "\n".join(lines)

    def _process_toml(self, content: str) -> str:
        """Process TOML — try to parse, fallback to first 50 lines."""
        try:
            import tomllib

            data = tomllib.loads(content)
            schema = self._extract_json_schema(data, depth=0, max_depth=3)
            return "[TOML Schema]\n" + json.dumps(schema, indent=2)
        except Exception:
            pass
        lines = content.split("\n")[:50]
        return "[TOML — first 50 lines]\n" + "\n".join(lines)
