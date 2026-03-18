"""tokenpak.agent.dashboard.export_api — HTTP handler for /v1/export/csv.

Integrates with the proxy server's BaseHTTPRequestHandler style.
Reads request body JSON, generates CSV via CSVExporter, and returns
a proper download response with Content-Disposition.

Protocol (POST /v1/export/csv):
    Request body (JSON):
        {
          "format": "full" | "simplified",   # default: "full"
          "data_type": "traces" | "stats"    # default: "traces"
        }
    Response (200 OK):
        Content-Type: text/csv
        Content-Disposition: attachment; filename="tokenpak-export-..."
        <CSV bytes>

    Error (400 Bad Request):
        Content-Type: application/json
        {"error": "...", "detail": "..."}
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .export_csv import CSVExporter, ExportDataType, ExportFormat


class ExportAPI:
    """Handles POST /v1/export/csv requests.

    Usage (from _ProxyHandler.do_POST)::

        body, status, headers = ExportAPI.handle(
            raw_body=body_bytes,
            traces=[t.to_dict() for t in ps.trace_storage.get_all()],
            session_stats=ps.session_stats(),
        )
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    """

    @staticmethod
    def handle(
        raw_body: bytes,
        traces: Optional[List[Dict[str, Any]]] = None,
        session_stats: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bytes, int, Dict[str, str]]:
        """Process a /v1/export/csv request.

        Returns:
            (response_body, http_status_code, headers_dict)
        """
        # 1. Parse request body
        try:
            params: Dict[str, Any] = json.loads(raw_body) if raw_body.strip() else {}
        except json.JSONDecodeError as exc:
            return ExportAPI._error(400, "invalid_json", str(exc))

        # 2. Validate + coerce params
        raw_fmt = params.get("format", "full")
        raw_dtype = params.get("data_type", "traces")

        try:
            fmt = ExportFormat(raw_fmt)
        except ValueError:
            return ExportAPI._error(
                400,
                "invalid_format",
                f"format must be 'full' or 'simplified', got: {raw_fmt!r}",
            )

        try:
            data_type = ExportDataType(raw_dtype)
        except ValueError:
            return ExportAPI._error(
                400,
                "invalid_data_type",
                f"data_type must be 'traces' or 'stats', got: {raw_dtype!r}",
            )

        # 3. Generate CSV
        try:
            exporter = CSVExporter(
                traces=traces or [],
                session_stats=session_stats or {},
            )
            csv_bytes, filename = exporter.export(data_type=data_type, fmt=fmt)
        except Exception as exc:
            return ExportAPI._error(500, "export_failed", str(exc))

        # 4. Build response headers
        headers = {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(csv_bytes)),
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Disposition",
        }

        return csv_bytes, 200, headers

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _error(status: int, code: str, detail: str) -> Tuple[bytes, int, Dict[str, str]]:
        body = json.dumps({"error": code, "detail": detail}, indent=2).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Access-Control-Allow-Origin": "*",
        }
        return body, status, headers
