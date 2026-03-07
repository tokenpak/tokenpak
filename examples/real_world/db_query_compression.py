"""
Database Query Results Compression
====================================
Compress large database result sets before passing to LLMs for analysis.

Problem: SQL query results (especially verbose JSON/text columns) can blow token budgets.
Solution: Compress result sets while preserving structure and key data.

Expected savings: 40-65% on typical database output.
Setup: pip install tokenpak
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


engine = HeuristicEngine()


# Simulated database query results (what you'd get from cursor.fetchall())
DB_RESULTS = [
    {
        "id": 1,
        "customer_name": "Acme Corporation",
        "status": "active",
        "notes": """
            Customer has been with us since 2019. They initially started with the basic plan
            and have gradually upgraded over time. The customer success team has noted that
            they are generally very satisfied with the product, although they have raised some
            concerns about the reporting features in Q3 2024. Their primary contact is Sarah
            Johnson in the procurement department. Payment is always on time. They have expressed
            interest in the enterprise features and may upgrade in Q1 2025.
        """,
        "revenue_usd": 45000,
    },
    {
        "id": 2,
        "customer_name": "TechStart Inc.",
        "status": "churned",
        "notes": """
            Customer churned in November 2024 after 18 months. Exit survey indicated that
            pricing was the primary concern — they felt the value proposition was not strong
            enough for their stage. Secondary reason was lack of integrations with their
            existing stack (they use Notion, Linear, and Slack heavily). The customer was
            on the Growth plan. No payment issues during their tenure. They expressed
            willingness to return if pricing improves or if we add Notion integration.
        """,
        "revenue_usd": 12000,
    },
    {
        "id": 3,
        "customer_name": "Global Logistics Ltd.",
        "status": "trial",
        "notes": """
            Currently in 30-day trial. Very high engagement — daily active usage. The team
            is evaluating three vendors simultaneously. Our main competition is CompetitorX
            and an internal tool they are considering building. The champion is Marcus Lee,
            VP Engineering. He is technically sophisticated and has asked detailed questions
            about our API and data residency. Trial started December 1, 2024. Decision
            expected by January 15, 2025. Win probability estimated at 65%.
        """,
        "revenue_usd": 0,
    },
]


def compress_db_results(results: list[dict], text_fields: list[str] = None) -> dict:
    """
    Compress text fields in database results for LLM analysis.
    
    Args:
        results: List of row dicts from database query
        text_fields: Which fields to compress (auto-detects long strings if None)
    
    Returns:
        Compressed results with savings statistics
    """
    if text_fields is None:
        # Auto-detect: compress string fields > 100 chars
        sample = results[0] if results else {}
        text_fields = [k for k, v in sample.items() if isinstance(v, str) and len(v) > 100]

    print(f"  Compressing fields: {text_fields}")
    print(f"  Rows: {len(results)}\n")

    total_original_chars = 0
    total_compressed_chars = 0
    compressed_results = []

    for row in results:
        compressed_row = dict(row)
        for field in text_fields:
            if field in row and isinstance(row[field], str):
                original = row[field].strip()
                compressed = engine.compact(original)
                compressed_row[field] = compressed
                total_original_chars += len(original)
                total_compressed_chars += len(compressed)
        compressed_results.append(compressed_row)

    original_tokens = total_original_chars // 4
    compressed_tokens = total_compressed_chars // 4
    savings_pct = (1 - total_compressed_chars / total_original_chars) * 100 if total_original_chars > 0 else 0

    return {
        "rows": compressed_results,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "savings_pct": savings_pct,
        "text_fields_compressed": text_fields,
    }


def format_for_llm(results: list[dict], query_description: str) -> str:
    """Format compressed DB results for LLM prompt injection."""
    rows_text = ""
    for i, row in enumerate(results, 1):
        rows_text += f"\nRow {i}:\n"
        for k, v in row.items():
            rows_text += f"  {k}: {v}\n"

    return f"""Database Query: {query_description}
Results ({len(results)} rows):
{rows_text}"""


def main():
    print("=== Database Query Results Compression ===\n")

    print("📥 Compressing query results (notes field)...")
    result = compress_db_results(DB_RESULTS, text_fields=["notes"])

    print(f"  Original:   ~{result['original_tokens']} tokens ({sum(len(str(r)) for r in DB_RESULTS)} chars total)")
    print(f"  Compressed: ~{result['compressed_tokens']} tokens")
    print(f"  Savings:    {result['savings_pct']:.0f}%\n")

    print("📊 Per-row compression:\n")
    for original, compressed in zip(DB_RESULTS, result["rows"]):
        orig_len = len(original["notes"].strip())
        comp_len = len(compressed["notes"])
        savings = (1 - comp_len / orig_len) * 100
        print(f"  [{original['customer_name']}] notes: {orig_len}→{comp_len} chars ({savings:.0f}% saved)")

    print("\n📝 LLM-ready formatted output:\n")
    formatted = format_for_llm(
        result["rows"],
        "SELECT id, customer_name, status, notes, revenue_usd FROM customers WHERE created_at > '2019-01-01'"
    )
    print(formatted[:600] + "...\n")
    print(f"  Total prompt snippet: ~{len(formatted) // 4} tokens")


if __name__ == "__main__":
    main()
