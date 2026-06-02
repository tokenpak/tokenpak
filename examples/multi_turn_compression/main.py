"""
Multi-Turn Conversation Compression Example
============================================
Demonstrates compressing conversation history to keep context window usage low.

Problem: Long chat histories consume huge token budgets, leaving less room for
new content and increasing API costs dramatically.

Solution: Compress older turns while keeping recent turns intact. Use
HeuristicEngine with a sliding window strategy.

Expected compression: varies by input; measure in your own workflow.
Setup: pip install tokenpak
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def compress_conversation_history(
    messages: list[dict],
    max_tokens: int = 2000,
    recent_turns_to_keep: int = 3,
) -> list[dict]:
    """
    Compress older conversation turns to fit within a token budget.

    Strategy:
    - Keep the last N turns fully intact (recent context matters most)
    - Compress older turns with HeuristicEngine
    - Never compress system messages (they define behavior)

    Args:
        messages: List of {"role": str, "content": str} dicts
        max_tokens: Target token budget for conversation history
        recent_turns_to_keep: Number of recent user/assistant pairs to preserve

    Returns:
        Compressed messages list
    """
    engine = HeuristicEngine()

    # Calculate current total tokens
    total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

    if total_tokens <= max_tokens:
        return messages  # Already fits — no compression needed

    # Split into system, recent, and older messages
    system_msgs = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    # Keep last N turns (user+assistant pairs = 2 messages per turn)
    keep_count = recent_turns_to_keep * 2
    recent = non_system[-keep_count:] if len(non_system) > keep_count else non_system
    older = non_system[:-keep_count] if len(non_system) > keep_count else []

    # Compress older messages
    compressed_older = []
    for msg in older:
        hints = CompactionHints(
            target_tokens=estimate_tokens(msg["content"]) // 2,  # Aim for 50% reduction
            keep_headers=True,
            keep_code_blocks=True,
        )
        compressed_content = engine.compact(msg["content"], hints)
        compressed_older.append({
            "role": msg["role"],
            "content": compressed_content,
            "_compressed": True,
        })

    return system_msgs + compressed_older + recent


def run_example():
    """Simulate a long conversation and compress it."""
    print("=== Multi-Turn Conversation Compression ===\n")

    # Simulate a long conversation about building a web app
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant specializing in Python web development.",
        },
        {
            "role": "user",
            "content": "Can you help me build a REST API? I need to understand the basics of Flask and how to structure my project properly.",
        },
        {
            "role": "assistant",
            "content": (
                "Absolutely! Flask is a lightweight Python web framework that makes "
                "building REST APIs straightforward. Let me walk you through the key concepts.\n\n"
                "First, install Flask: `pip install flask`\n\n"
                "A basic Flask app looks like this:\n\n"
                "```python\nfrom flask import Flask, jsonify\napp = Flask(__name__)\n\n"
                "@app.route('/hello')\ndef hello():\n    return jsonify({'message': 'Hello!'})\n\n"
                "if __name__ == '__main__':\n    app.run(debug=True)\n```\n\n"
                "For project structure, I recommend: routes/, models/, services/, and a config.py."
            ),
        },
        {
            "role": "user",
            "content": "Great! Now how do I add database support? I want to use PostgreSQL.",
        },
        {
            "role": "assistant",
            "content": (
                "For PostgreSQL with Flask, SQLAlchemy is the standard ORM. "
                "Install with: `pip install flask-sqlalchemy psycopg2-binary`\n\n"
                "Configure it in your app:\n\n"
                "```python\nfrom flask_sqlalchemy import SQLAlchemy\n\n"
                "app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://user:pass@localhost/dbname'\n"
                "db = SQLAlchemy(app)\n```\n\n"
                "Define models as Python classes:\n\n"
                "```python\nclass User(db.Model):\n    id = db.Column(db.Integer, primary_key=True)\n"
                "    username = db.Column(db.String(80), unique=True)\n    email = db.Column(db.String(120))\n```\n\n"
                "Run `db.create_all()` to create tables. Use `db.session.add()` and `db.session.commit()` for writes."
            ),
        },
        {
            "role": "user",
            "content": "How do I handle authentication? JWT seems popular.",
        },
        {
            "role": "assistant",
            "content": (
                "JWT (JSON Web Tokens) is a great choice for stateless API auth. "
                "Use `flask-jwt-extended`: `pip install flask-jwt-extended`\n\n"
                "```python\nfrom flask_jwt_extended import JWTManager, create_access_token, jwt_required\n\n"
                "app.config['JWT_SECRET_KEY'] = 'your-secret-key'\njwt = JWTManager(app)\n\n"
                "@app.route('/login', methods=['POST'])\ndef login():\n    # validate credentials...\n"
                "    token = create_access_token(identity=user.id)\n    return jsonify(access_token=token)\n\n"
                "@app.route('/protected')\n@jwt_required()\ndef protected():\n    return jsonify({'data': 'secret'})\n```"
            ),
        },
        # Recent turns — these should stay intact
        {
            "role": "user",
            "content": "Now I want to add rate limiting to prevent abuse. What's the best approach?",
        },
        {
            "role": "assistant",
            "content": (
                "Flask-Limiter is the go-to solution. Install: `pip install flask-limiter`\n\n"
                "```python\nfrom flask_limiter import Limiter\nfrom flask_limiter.util import get_remote_address\n\n"
                "limiter = Limiter(app=app, key_func=get_remote_address, default_limits=['200/day', '50/hour'])\n\n"
                "@app.route('/api/data')\n@limiter.limit('10/minute')\ndef get_data():\n    return jsonify({'data': '...'})\n```"
            ),
        },
        {
            "role": "user",
            "content": "Perfect. Can you show me how to write tests for the rate limiting?",
        },
    ]

    # Show before stats
    total_before = sum(estimate_tokens(m["content"]) for m in messages)
    print(f"Before compression: {len(messages)} messages, ~{total_before} tokens\n")

    # Compress
    compressed = compress_conversation_history(
        messages,
        max_tokens=1500,
        recent_turns_to_keep=2,
    )

    total_after = sum(estimate_tokens(m["content"]) for m in compressed)
    print(f"After compression:  {len(compressed)} messages, ~{total_after} tokens")
    print(f"Savings:            {1 - total_after/total_before:.0%}\n")

    # Show which messages were compressed
    print("Message breakdown:")
    for i, msg in enumerate(compressed):
        role = msg["role"]
        tokens = estimate_tokens(msg["content"])
        compressed_flag = " [COMPRESSED]" if msg.get("_compressed") else ""
        print(f"  [{i+1}] {role:10s} ~{tokens:4d} tokens{compressed_flag}")

    # Show a compressed message vs original
    print("\n--- Original turn 2 (assistant) ---")
    print(messages[2]["content"][:200] + "...")
    print("\n--- Compressed turn 2 (assistant) ---")
    print(compressed[1]["content"])


if __name__ == "__main__":
    run_example()
    print("\n✅ Multi-turn compression example complete!")
