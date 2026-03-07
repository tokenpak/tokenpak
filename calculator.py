#!/usr/bin/env python3
"""
TokenPak Cost Calculator — ROI Dashboard
=========================================
Shows users how much they're saving with TokenPak via compression,
caching, and smart routing.

Run:  python3 calculator.py
Open: http://localhost:5000
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEMETRY_DB = Path(os.environ.get(
    "TOKENPAK_TELEMETRY_DB",
    Path.home() / ".tokenpak" / "telemetry.db",
))

PRICING_CATALOG_PATH = (
    Path(__file__).parent / "tokenpak" / "telemetry" / "data" / "pricing_catalog.json"
)

PORT = int(os.environ.get("CALCULATOR_PORT", 5000))

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def load_pricing() -> dict[str, dict]:
    if PRICING_CATALOG_PATH.exists():
        with open(PRICING_CATALOG_PATH) as f:
            data = json.load(f)
        return data.get("models", {})
    # Fallback minimal catalog (USD per 1M tokens)
    return {
        "claude-opus-4-6":   {"provider": "anthropic", "input": 15.0,  "output": 75.0,  "cache_read": 1.5},
        "claude-sonnet-4-6": {"provider": "anthropic", "input": 3.0,   "output": 15.0,  "cache_read": 0.3},
        "claude-haiku-4-5":  {"provider": "anthropic", "input": 0.8,   "output": 4.0,   "cache_read": 0.08},
        "gpt-4o":            {"provider": "openai",    "input": 2.5,   "output": 10.0,  "cache_read": None},
        "gpt-4o-mini":       {"provider": "openai",    "input": 0.15,  "output": 0.6,   "cache_read": None},
        "gemini-2-flash":    {"provider": "google",    "input": 0.1,   "output": 0.4,   "cache_read": None},
    }

PRICING = load_pricing()

def cost_per_token(model: str, token_type: str = "input") -> float:
    """Return USD per token for a model (pricing is per 1M, we return per 1)."""
    entry = PRICING.get(model) or {}
    per_million = entry.get(token_type) or 0.0
    return per_million / 1_000_000.0

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(TELEMETRY_DB))
    conn.row_factory = sqlite3.Row
    return conn

def seed_sample_data(conn: sqlite3.Connection) -> None:
    """Seed realistic-looking sample data so the dashboard is useful out of the box."""
    import random, hashlib

    random.seed(42)
    now = time.time()
    models = [
        ("claude-sonnet-4-6", "anthropic", "/v1/messages"),
        ("claude-haiku-4-5",  "anthropic", "/v1/messages"),
        ("claude-opus-4-6",   "anthropic", "/v1/messages"),
        ("gpt-4o",            "openai",    "/v1/chat/completions"),
        ("gpt-4o-mini",       "openai",    "/v1/chat/completions"),
    ]

    events_rows = []
    usage_rows  = []
    costs_rows  = []

    for day_offset in range(7):
        day_ts = now - (6 - day_offset) * 86400
        for _ in range(random.randint(80, 200)):
            model, provider, api = random.choice(models)
            trace_id  = hashlib.md5(f"{day_ts}{random.random()}".encode()).hexdigest()
            req_id    = hashlib.md5(f"req{trace_id}".encode()).hexdigest()
            ts        = day_ts + random.random() * 86400
            dur       = random.uniform(200, 4000)

            # Token counts: baseline (what user would've sent), actual (compressed)
            baseline_input = random.randint(2000, 50000)
            compression    = random.uniform(0.20, 0.60)   # 20-60% compression
            cache_hit_prob = 0.25
            cache_read_tok = int(baseline_input * random.uniform(0.1, 0.4)) if random.random() < cache_hit_prob else 0
            actual_input   = int(baseline_input * (1 - compression)) - cache_read_tok
            actual_input   = max(actual_input, 100)
            output_tok     = random.randint(200, 3000)
            total_tokens   = baseline_input + output_tok

            # Cost calculations
            in_price  = cost_per_token(model, "input")
            out_price = cost_per_token(model, "output")
            cr_price  = cost_per_token(model, "cache_read") if PRICING.get(model, {}).get("cache_read") else in_price * 0.1

            baseline_cost = baseline_input * in_price + output_tok * out_price
            cache_cost    = cache_read_tok * cr_price
            actual_cost   = actual_input  * in_price + output_tok * out_price + cache_cost
            savings       = baseline_cost - actual_cost

            events_rows.append((
                trace_id, req_id, "request_end", ts, provider, model,
                "openclaw", api, "end_turn", "", dur, "ok", None, "{}", "", "",
            ))
            usage_rows.append((
                trace_id, "proxy", "high",
                actual_input, output_tok, baseline_input, output_tok,
                cache_read_tok, 0, total_tokens, actual_input + output_tok,
                baseline_input + output_tok, "{}",
            ))
            costs_rows.append((
                trace_id,
                actual_input * in_price, output_tok * out_price, cache_cost, 0.0, actual_cost,
                "catalog", "v1",
                baseline_input, actual_input, output_tok,
                baseline_cost, actual_cost, savings, savings, 0.0,
            ))

    conn.executemany(
        """INSERT OR IGNORE INTO tp_events
           (trace_id, request_id, event_type, ts, provider, model, agent_id, api,
            stop_reason, session_id, duration_ms, status, error_class, payload, span_id, node_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        events_rows,
    )
    conn.executemany(
        """INSERT OR IGNORE INTO tp_usage
           (trace_id, usage_source, confidence,
            input_billed, output_billed, input_est, output_est,
            cache_read, cache_write, total_tokens, total_tokens_billed,
            total_tokens_est, provider_usage_raw)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        usage_rows,
    )
    conn.executemany(
        """INSERT OR IGNORE INTO tp_costs
           (trace_id, cost_input, cost_output, cost_cache_read, cost_cache_write,
            cost_total, cost_source, pricing_version,
            baseline_input_tokens, actual_input_tokens, output_tokens,
            baseline_cost, actual_cost, savings_total, savings_qmd, savings_tp)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        costs_rows,
    )
    conn.commit()

def ensure_data() -> None:
    """Seed sample data if DB is empty."""
    if not TELEMETRY_DB.exists():
        return
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tp_events").fetchone()[0]
        if count == 0:
            print("  [calculator] DB is empty — seeding sample data for demo…")
            seed_sample_data(conn)
        elif count == 1:
            # Only the test row — seed alongside it
            print("  [calculator] Only 1 row (test trace) — seeding sample data…")
            seed_sample_data(conn)

# ---------------------------------------------------------------------------
# Metrics queries
# ---------------------------------------------------------------------------

PERIOD_DAYS = {"day": 1, "week": 7, "month": 30, "all": 3650}


def query_metrics(period: str = "week", model: Optional[str] = None) -> dict[str, Any]:
    days   = PERIOD_DAYS.get(period, 7)
    since  = time.time() - days * 86400
    params: list[Any] = [since]
    model_clause = ""
    if model:
        model_clause = " AND e.model = ?"
        params.append(model)

    with get_db() as conn:
        # --- Summary totals ---
        row = conn.execute(f"""
            SELECT
                COALESCE(SUM(c.baseline_cost), 0)    AS total_cost_before,
                COALESCE(SUM(c.actual_cost),   0)    AS total_cost_after,
                COALESCE(SUM(c.savings_total), 0)    AS total_saved,
                COALESCE(SUM(u.total_tokens),  0)    AS total_tokens,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) AS actual_tokens,
                COALESCE(SUM(u.cache_read),    0)    AS cache_read_tokens,
                COUNT(DISTINCT e.trace_id)           AS total_requests,
                COALESCE(SUM(c.baseline_input_tokens), 0) AS baseline_input_tokens,
                COALESCE(SUM(c.actual_input_tokens),   0) AS actual_input_tokens
            FROM tp_events e
            JOIN tp_costs c ON c.trace_id = e.trace_id
            JOIN tp_usage u ON u.trace_id = e.trace_id
            WHERE e.ts > ?{model_clause}
        """, params).fetchone()

        total_cost_before     = row["total_cost_before"]
        total_cost_after      = row["total_cost_after"]
        total_saved           = row["total_saved"]
        total_tokens          = row["total_tokens"]
        actual_tokens         = row["actual_tokens"]
        cache_read_tokens     = row["cache_read_tokens"]
        total_requests        = row["total_requests"]
        baseline_input        = row["baseline_input_tokens"]
        actual_input          = row["actual_input_tokens"]

        compression_ratio = (
            (baseline_input - actual_input) / baseline_input * 100
            if baseline_input > 0 else 0.0
        )
        cache_hit_rate = (
            cache_read_tokens / baseline_input * 100
            if baseline_input > 0 else 0.0
        )
        monthly_projection = (
            total_saved / days * 30
            if days > 0 and total_saved > 0 else 0.0
        )

        # --- By model ---
        model_rows = conn.execute(f"""
            SELECT e.model,
                   COUNT(DISTINCT e.trace_id)         AS requests,
                   SUM(c.savings_total)               AS saved,
                   SUM(c.baseline_cost)               AS cost_before,
                   SUM(c.actual_cost)                 AS cost_after,
                   SUM(c.baseline_input_tokens)       AS baseline_tok,
                   SUM(c.actual_input_tokens)         AS actual_tok,
                   SUM(u.cache_read)                  AS cache_read
            FROM tp_events e
            JOIN tp_costs c ON c.trace_id = e.trace_id
            JOIN tp_usage u ON u.trace_id = e.trace_id
            WHERE e.ts > ?{model_clause}
            GROUP BY e.model
            ORDER BY saved DESC
        """, params).fetchall()

        by_model = {}
        for r in model_rows:
            b  = r["baseline_tok"] or 0
            a  = r["actual_tok"] or 0
            cr = r["cache_read"] or 0
            by_model[r["model"]] = {
                "requests":          r["requests"],
                "saved":             round(r["saved"] or 0, 6),
                "cost_before":       round(r["cost_before"] or 0, 6),
                "cost_after":        round(r["cost_after"] or 0, 6),
                "compression_ratio": round((b - a) / b * 100 if b > 0 else 0, 2),
                "cache_hit_rate":    round(cr / b * 100 if b > 0 else 0, 2),
            }

        # --- By endpoint ---
        endpoint_rows = conn.execute(f"""
            SELECT e.api,
                   COUNT(DISTINCT e.trace_id) AS requests,
                   SUM(c.savings_total)        AS saved
            FROM tp_events e
            JOIN tp_costs c ON c.trace_id = e.trace_id
            JOIN tp_usage u ON u.trace_id = e.trace_id
            WHERE e.ts > ?{model_clause}
            GROUP BY e.api
            ORDER BY saved DESC
            LIMIT 5
        """, params).fetchall()

        by_endpoint = {
            r["api"]: {"requests": r["requests"], "saved": round(r["saved"] or 0, 6)}
            for r in endpoint_rows
        }

        # --- Daily trend (last 7 days) ---
        trend_days = min(days, 30)
        trend_since = time.time() - trend_days * 86400
        trend_params = [trend_since] + ([model] if model else [])
        trend_rows = conn.execute(f"""
            SELECT date(e.ts, 'unixepoch', 'localtime') AS day,
                   SUM(c.savings_total)  AS saved,
                   SUM(c.baseline_cost)  AS cost_before,
                   SUM(c.actual_cost)    AS cost_after,
                   COUNT(DISTINCT e.trace_id) AS requests
            FROM tp_events e
            JOIN tp_costs c ON c.trace_id = e.trace_id
            JOIN tp_usage u ON u.trace_id = e.trace_id
            WHERE e.ts > ?{model_clause}
            GROUP BY day
            ORDER BY day
        """, trend_params).fetchall()

        daily_trend = [
            {
                "date":        r["day"],
                "saved":       round(r["saved"] or 0, 6),
                "cost_before": round(r["cost_before"] or 0, 6),
                "cost_after":  round(r["cost_after"] or 0, 6),
                "requests":    r["requests"],
            }
            for r in trend_rows
        ]

    return {
        "period":              period,
        "total_requests":      total_requests,
        "total_cost_before":   round(total_cost_before, 4),
        "total_cost_after":    round(total_cost_after,  4),
        "total_saved":         round(total_saved,       4),
        "compression_ratio":   round(compression_ratio, 2),
        "cache_hit_rate":      round(cache_hit_rate,    2),
        "monthly_projection":  round(monthly_projection, 2),
        "by_model":            by_model,
        "by_endpoint":         by_endpoint,
        "daily_trend":         daily_trend,
        "db_path":             str(TELEMETRY_DB),
        "generated_at":        datetime.utcnow().isoformat() + "Z",
    }

# ---------------------------------------------------------------------------
# HTML dashboard (single-page, embedded CSS + JS)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>TokenPak ROI Calculator</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg:      #0d1117;
  --surface: #161b22;
  --border:  #30363d;
  --accent:  #58a6ff;
  --green:   #3fb950;
  --red:     #f85149;
  --muted:   #8b949e;
  --text:    #e6edf3;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); min-height: 100vh; }
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 1rem 2rem;
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 1rem;
}
header h1 { font-size: 1.3rem; color: var(--accent); }
header span { color: var(--muted); font-size: 0.85rem; }
.controls {
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 0.75rem 2rem; display: flex; gap: 1rem; flex-wrap: wrap; align-items: center;
}
.controls label { color: var(--muted); font-size: 0.85rem; }
select, input {
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 0.4rem 0.75rem; font-size: 0.9rem; cursor: pointer;
}
select:focus, input:focus { outline: 2px solid var(--accent); }
button {
  background: var(--accent); color: #000; border: none; border-radius: 6px;
  padding: 0.4rem 1rem; font-size: 0.85rem; cursor: pointer; font-weight: 600;
}
button:hover { opacity: 0.85; }
button.secondary {
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
}
main { padding: 2rem; max-width: 1400px; margin: 0 auto; }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem; margin-bottom: 2rem;
}
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.5rem;
}
.card .label { color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 0.5rem; }
.card .value { font-size: 2rem; font-weight: 700; }
.card .value.green { color: var(--green); }
.card .value.accent { color: var(--accent); }
.card .sub { color: var(--muted); font-size: 0.8rem; margin-top: 0.25rem; }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 2rem; }
@media (max-width: 900px) { .charts { grid-template-columns: 1fr; } }
.chart-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.5rem;
}
.chart-box h3 { font-size: 0.95rem; margin-bottom: 1rem; color: var(--muted); }
.chart-box canvas { max-height: 260px; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
thead th {
  text-align: left; padding: 0.6rem 1rem;
  color: var(--muted); font-size: 0.8rem; text-transform: uppercase;
  border-bottom: 1px solid var(--border);
}
tbody td { padding: 0.7rem 1rem; border-bottom: 1px solid var(--border); }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: rgba(88,166,255,.05); }
.pill {
  display: inline-block; padding: 0.15rem 0.5rem;
  border-radius: 100px; font-size: 0.78rem; font-weight: 600;
}
.pill.green { background: rgba(63,185,80,.2); color: var(--green); }
.pill.blue  { background: rgba(88,166,255,.2); color: var(--accent); }
.section { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }
.section h3 { font-size: 0.95rem; margin-bottom: 1rem; color: var(--muted); }
#status { padding: 1rem 2rem; text-align: center; color: var(--muted); font-size: 0.85rem; }
.loading { animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
</style>
</head>
<body>
<header>
  <h1>⚡ TokenPak ROI Calculator</h1>
  <span id="db-path"></span>
</header>
<div class="controls">
  <label>Period:
    <select id="period">
      <option value="day">Today</option>
      <option value="week" selected>Last 7 Days</option>
      <option value="month">Last 30 Days</option>
      <option value="all">All Time</option>
    </select>
  </label>
  <label>Model:
    <select id="model-filter">
      <option value="">All Models</option>
    </select>
  </label>
  <button onclick="refresh()">Refresh</button>
  <button class="secondary" onclick="exportCSV()">⬇ Export CSV</button>
</div>
<div id="status" class="loading">Loading metrics…</div>
<main id="main" style="display:none">
  <div class="cards">
    <div class="card">
      <div class="label">Total Saved</div>
      <div class="value green" id="c-saved">—</div>
      <div class="sub" id="c-saved-sub">—</div>
    </div>
    <div class="card">
      <div class="label">Compression Ratio</div>
      <div class="value accent" id="c-compression">—</div>
      <div class="sub">tokens eliminated via QMD</div>
    </div>
    <div class="card">
      <div class="label">Cache Hit Rate</div>
      <div class="value accent" id="c-cache">—</div>
      <div class="sub">tokens served from cache</div>
    </div>
    <div class="card">
      <div class="label">Monthly Projection</div>
      <div class="value green" id="c-monthly">—</div>
      <div class="sub">at current usage rate</div>
    </div>
    <div class="card">
      <div class="label">Total Requests</div>
      <div class="value" id="c-reqs">—</div>
      <div class="sub" id="c-period">—</div>
    </div>
    <div class="card">
      <div class="label">Cost Before / After</div>
      <div class="value" id="c-costs">—</div>
      <div class="sub">without vs. with TokenPak</div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-box">
      <h3>Savings by Model</h3>
      <canvas id="chart-model"></canvas>
    </div>
    <div class="chart-box">
      <h3>Savings Trend (USD/day)</h3>
      <canvas id="chart-trend"></canvas>
    </div>
  </div>

  <div class="section">
    <h3>Top Endpoints by Savings</h3>
    <table>
      <thead><tr><th>Endpoint</th><th>Requests</th><th>Saved</th></tr></thead>
      <tbody id="endpoint-table"></tbody>
    </table>
  </div>

  <div class="section">
    <h3>Model Breakdown</h3>
    <table>
      <thead><tr>
        <th>Model</th><th>Requests</th>
        <th>Cost Before</th><th>Cost After</th><th>Saved</th>
        <th>Compression</th><th>Cache Hit</th>
      </tr></thead>
      <tbody id="model-table"></tbody>
    </table>
  </div>
  <div id="generated-at" style="color:var(--muted);font-size:0.78rem;text-align:right;margin-top:1rem;"></div>
</main>

<script>
let modelChart, trendChart;

function fmt(usd) {
  if (usd >= 1000) return '$' + (usd/1000).toFixed(2) + 'k';
  if (usd >= 1)    return '$' + usd.toFixed(4);
  return '$' + usd.toFixed(6);
}
function fmtPct(v) { return v.toFixed(1) + '%'; }
function fmtN(n) { return n.toLocaleString(); }

async function fetchMetrics() {
  const period = document.getElementById('period').value;
  const model  = document.getElementById('model-filter').value;
  const qs = new URLSearchParams({period});
  if (model) qs.set('model', model);
  const r = await fetch('/api/metrics?' + qs);
  return r.json();
}

async function refresh() {
  document.getElementById('status').style.display = 'block';
  document.getElementById('status').className = 'loading';
  document.getElementById('status').textContent = 'Loading metrics…';
  document.getElementById('main').style.display = 'none';

  const d = await fetchMetrics();

  // Populate model filter
  const sel = document.getElementById('model-filter');
  const cur = sel.value;
  const models = Object.keys(d.by_model);
  sel.innerHTML = '<option value="">All Models</option>' +
    models.map(m => `<option value="${m}"${m===cur?' selected':''}>${m}</option>`).join('');

  // Summary cards
  document.getElementById('c-saved').textContent     = fmt(d.total_saved);
  document.getElementById('c-saved-sub').textContent = `${fmt(d.total_cost_before)} → ${fmt(d.total_cost_after)}`;
  document.getElementById('c-compression').textContent = fmtPct(d.compression_ratio);
  document.getElementById('c-cache').textContent       = fmtPct(d.cache_hit_rate);
  document.getElementById('c-monthly').textContent     = fmt(d.monthly_projection) + '/mo';
  document.getElementById('c-reqs').textContent        = fmtN(d.total_requests);
  document.getElementById('c-period').textContent      = `over selected period (${d.period})`;
  document.getElementById('c-costs').textContent       = `${fmt(d.total_cost_before)} / ${fmt(d.total_cost_after)}`;
  document.getElementById('db-path').textContent       = d.db_path;
  document.getElementById('generated-at').textContent  = 'Generated: ' + d.generated_at;

  // Model chart
  const mLabels = models;
  const mSaved  = models.map(m => d.by_model[m].saved);
  const mColors = models.map((_, i) => `hsl(${200 + i * 40}, 70%, 55%)`);
  if (modelChart) modelChart.destroy();
  modelChart = new Chart(document.getElementById('chart-model'), {
    type: 'bar',
    data: { labels: mLabels, datasets: [{ label: 'Saved (USD)', data: mSaved, backgroundColor: mColors, borderRadius: 6 }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: { ticks: { color: '#8b949e', callback: v => fmt(v) }, grid: { color: '#30363d' } },
        x: { ticks: { color: '#8b949e' }, grid: { display: false } },
      },
    },
  });

  // Trend chart
  const tDates  = d.daily_trend.map(r => r.date);
  const tSaved  = d.daily_trend.map(r => r.saved);
  const tBefore = d.daily_trend.map(r => r.cost_before);
  const tAfter  = d.daily_trend.map(r => r.cost_after);
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(document.getElementById('chart-trend'), {
    type: 'line',
    data: {
      labels: tDates,
      datasets: [
        { label: 'Saved', data: tSaved, borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,.15)', tension: 0.4, fill: true },
        { label: 'Before', data: tBefore, borderColor: '#58a6ff', borderDash: [4,4], tension: 0.4 },
        { label: 'After',  data: tAfter,  borderColor: '#f85149', borderDash: [4,4], tension: 0.4 },
      ],
    },
    options: {
      plugins: { legend: { labels: { color: '#8b949e' } } },
      scales: {
        y: { ticks: { color: '#8b949e', callback: v => fmt(v) }, grid: { color: '#30363d' } },
        x: { ticks: { color: '#8b949e' }, grid: { display: false } },
      },
    },
  });

  // Endpoint table
  document.getElementById('endpoint-table').innerHTML = Object.entries(d.by_endpoint)
    .map(([ep, v]) => `<tr>
      <td><code>${ep || '(unknown)'}</code></td>
      <td>${fmtN(v.requests)}</td>
      <td><span class="pill green">${fmt(v.saved)}</span></td>
    </tr>`).join('') || '<tr><td colspan="3" style="color:var(--muted);text-align:center">No data</td></tr>';

  // Model table
  document.getElementById('model-table').innerHTML = models
    .map(m => {
      const v = d.by_model[m];
      return `<tr>
        <td>${m}</td>
        <td>${fmtN(v.requests)}</td>
        <td>${fmt(v.cost_before)}</td>
        <td>${fmt(v.cost_after)}</td>
        <td><span class="pill green">${fmt(v.saved)}</span></td>
        <td><span class="pill blue">${fmtPct(v.compression_ratio)}</span></td>
        <td><span class="pill blue">${fmtPct(v.cache_hit_rate)}</span></td>
      </tr>`;
    }).join('') || '<tr><td colspan="7" style="color:var(--muted);text-align:center">No data</td></tr>';

  document.getElementById('status').style.display = 'none';
  document.getElementById('main').style.display = 'block';
}

async function exportCSV() {
  const period = document.getElementById('period').value;
  const model  = document.getElementById('model-filter').value;
  const qs = new URLSearchParams({period});
  if (model) qs.set('model', model);
  window.location = '/api/export/csv?' + qs;
}

document.getElementById('period').addEventListener('change', refresh);
document.getElementById('model-filter').addEventListener('change', refresh);
refresh();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/metrics")
def api_metrics():
    period = request.args.get("period", "week")
    model  = request.args.get("model") or None
    if period not in PERIOD_DAYS:
        period = "week"
    try:
        data = query_metrics(period=period, model=model)
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/export/csv")
def api_export_csv():
    period = request.args.get("period", "week")
    model  = request.args.get("model") or None
    if period not in PERIOD_DAYS:
        period = "week"
    data = query_metrics(period=period, model=model)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "value"])
    writer.writerow(["period",              data["period"]])
    writer.writerow(["total_requests",      data["total_requests"]])
    writer.writerow(["total_cost_before",   data["total_cost_before"]])
    writer.writerow(["total_cost_after",    data["total_cost_after"]])
    writer.writerow(["total_saved",         data["total_saved"]])
    writer.writerow(["compression_ratio_%", data["compression_ratio"]])
    writer.writerow(["cache_hit_rate_%",    data["cache_hit_rate"]])
    writer.writerow(["monthly_projection",  data["monthly_projection"]])
    writer.writerow([])
    writer.writerow(["--- By Model ---"])
    writer.writerow(["model", "requests", "cost_before", "cost_after", "saved", "compression_%", "cache_hit_%"])
    for m, v in data["by_model"].items():
        writer.writerow([m, v["requests"], v["cost_before"], v["cost_after"], v["saved"], v["compression_ratio"], v["cache_hit_rate"]])
    writer.writerow([])
    writer.writerow(["--- Daily Trend ---"])
    writer.writerow(["date", "cost_before", "cost_after", "saved", "requests"])
    for r in data["daily_trend"]:
        writer.writerow([r["date"], r["cost_before"], r["cost_after"], r["saved"], r["requests"]])

    filename = f"tokenpak-roi-{period}-{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"  TokenPak ROI Calculator")
    print(f"  DB:   {TELEMETRY_DB}")
    print(f"  URL:  http://localhost:{PORT}")
    print()
    if not TELEMETRY_DB.exists():
        print(f"  ⚠️  Telemetry DB not found at {TELEMETRY_DB}")
        print(f"     Set TOKENPAK_TELEMETRY_DB env var to point to your DB.")
    else:
        ensure_data()
    app.run(host="127.0.0.1", port=PORT, debug=False)
