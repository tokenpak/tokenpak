/**
 * ExportButton — TokenPak Dashboard CSV Export
 *
 * Sends a POST request to /v1/export/csv and triggers a browser download.
 *
 * Props:
 *   proxyUrl   — Base URL of the running tokenpak proxy (e.g. "http://localhost:8766")
 *   dataType   — "traces" | "stats"  (default: "traces")
 *   format     — "full" | "simplified"  (default: "full")
 *   label      — Button label  (default: "Export CSV")
 *
 * Usage:
 *   <ExportButton proxyUrl="http://localhost:8766" dataType="traces" format="full" />
 */

import React, { useState } from "react";

export type ExportDataType = "traces" | "stats";
export type ExportFormat = "full" | "simplified";

interface ExportButtonProps {
  proxyUrl: string;
  dataType?: ExportDataType;
  format?: ExportFormat;
  label?: string;
}

export const ExportButton: React.FC<ExportButtonProps> = ({
  proxyUrl,
  dataType = "traces",
  format = "full",
  label = "Export CSV",
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExport = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${proxyUrl}/v1/export/csv`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_type: dataType, format }),
      });

      if (!response.ok) {
        const errBody = await response.json().catch(() => ({}));
        throw new Error(errBody.detail ?? `HTTP ${response.status}`);
      }

      // Extract filename from Content-Disposition header
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
      const filename = filenameMatch ? filenameMatch[1] : "tokenpak-export.csv";

      // Trigger browser download
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Export failed";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="tokenpak-export">
      <button
        onClick={handleExport}
        disabled={loading}
        className="tokenpak-export-btn"
        title={`Export ${dataType} as ${format} CSV`}
      >
        {loading ? "Exporting…" : label}
      </button>
      {error && (
        <span className="tokenpak-export-error" role="alert">
          ⚠️ {error}
        </span>
      )}
    </div>
  );
};

export default ExportButton;
