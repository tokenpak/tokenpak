/**
 * FilterBar — TokenPak Dashboard Session Filter & Search
 *
 * Renders a filter bar above the session table. Calls GET /v1/sessions with
 * filter params and re-renders the table without a full page reload.
 *
 * Props:
 *   proxyUrl    — Base URL of the running tokenpak proxy (e.g. "http://localhost:8766")
 *   onResults   — Callback with filtered sessions + total count
 *
 * Filter fields:
 *   model       — Dropdown populated from /v1/sessions?limit=0 (distinct models)
 *   from / to   — ISO date inputs (defaults: last 7 days)
 *   status      — all | success | error | partial
 *
 * Usage:
 *   <FilterBar proxyUrl="http://localhost:8766" onResults={(r) => setSessions(r)} />
 */

import React, { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SessionStatus = "all" | "success" | "error" | "partial";

export interface SessionRow {
  id: number;
  timestamp: string;
  model: string;
  request_type: string | null;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  status_code: number;
  endpoint: string | null;
  compilation_mode: string | null;
}

export interface FilterResult {
  sessions: SessionRow[];
  total: number;
  limit: number;
  offset: number;
  models: string[];
}

export interface FilterBarProps {
  proxyUrl: string;
  onResults?: (result: FilterResult) => void;
  defaultLimit?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function sevenDaysAgo(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return toISODate(d);
}

function today(): string {
  return toISODate(new Date());
}

// ---------------------------------------------------------------------------
// FilterBar component
// ---------------------------------------------------------------------------

export const FilterBar: React.FC<FilterBarProps> = ({
  proxyUrl,
  onResults,
  defaultLimit = 50,
}) => {
  const [model, setModel] = useState<string>("");
  const [fromDate, setFromDate] = useState<string>(sevenDaysAgo());
  const [toDate, setToDate] = useState<string>(today());
  const [status, setStatus] = useState<SessionStatus>("all");
  const [limit] = useState<number>(defaultLimit);
  const [offset, setOffset] = useState<number>(0);

  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [resultCount, setResultCount] = useState<number | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // ------------------------------------------------------------------
  // Fetch sessions from /v1/sessions
  // ------------------------------------------------------------------

  const fetchSessions = useCallback(
    async (currentOffset = 0) => {
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      setError(null);

      const params = new URLSearchParams();
      if (model) params.set("model", model);
      if (fromDate) params.set("from", `${fromDate}T00:00:00`);
      if (toDate) params.set("to", `${toDate}T23:59:59`);
      if (status !== "all") params.set("status", status);
      params.set("limit", String(limit));
      params.set("offset", String(currentOffset));

      try {
        const res = await fetch(`${proxyUrl}/v1/sessions?${params.toString()}`, {
          signal: controller.signal,
        });

        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `HTTP ${res.status}`);
        }

        const data: FilterResult = await res.json();
        setResultCount(data.sessions.length);
        setTotalCount(data.total);

        // Populate model dropdown on first load
        if (data.models && data.models.length > 0) {
          setModels(data.models);
        }

        onResults?.(data);
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") return;
        const msg = err instanceof Error ? err.message : "Filter failed";
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [proxyUrl, model, fromDate, toDate, status, limit, onResults]
  );

  // Fetch distinct models on mount (models list comes back in every response)
  useEffect(() => {
    fetchSessions(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------
  // Handlers
  // ------------------------------------------------------------------

  const handleApply = (e: React.FormEvent) => {
    e.preventDefault();
    setOffset(0);
    fetchSessions(0);
  };

  const handleClear = () => {
    setModel("");
    setFromDate(sevenDaysAgo());
    setToDate(today());
    setStatus("all");
    setOffset(0);
    fetchSessions(0);
  };

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div className="tokenpak-filter-bar">
      <form onSubmit={handleApply} className="tokenpak-filter-form">
        {/* Model dropdown */}
        <label className="tokenpak-filter-field">
          <span>Model</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="tokenpak-filter-select"
          >
            <option value="">All models</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>

        {/* Date range */}
        <label className="tokenpak-filter-field">
          <span>From</span>
          <input
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="tokenpak-filter-input"
          />
        </label>

        <label className="tokenpak-filter-field">
          <span>To</span>
          <input
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="tokenpak-filter-input"
          />
        </label>

        {/* Status dropdown */}
        <label className="tokenpak-filter-field">
          <span>Status</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as SessionStatus)}
            className="tokenpak-filter-select"
          >
            <option value="all">All</option>
            <option value="success">Success (2xx)</option>
            <option value="error">Error (4xx/5xx)</option>
            <option value="partial">Partial (3xx)</option>
          </select>
        </label>

        {/* Actions */}
        <div className="tokenpak-filter-actions">
          <button
            type="submit"
            disabled={loading}
            className="tokenpak-filter-apply-btn"
          >
            {loading ? "Loading…" : "Apply Filters"}
          </button>
          <button
            type="button"
            onClick={handleClear}
            className="tokenpak-filter-clear-link"
          >
            Clear
          </button>
        </div>
      </form>

      {/* Result count */}
      {totalCount !== null && resultCount !== null && (
        <div className="tokenpak-filter-result-count" role="status">
          Showing {resultCount} of {totalCount} sessions
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="tokenpak-filter-error" role="alert">
          ⚠️ {error}
        </div>
      )}
    </div>
  );
};

export default FilterBar;
