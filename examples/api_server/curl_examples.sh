#!/usr/bin/env bash
# REST API cURL Examples
# ======================
# Requires the api_server running: python server.py
# Start server: cd examples/api_server && python server.py

BASE_URL="http://localhost:8000"

echo "=== TokenPak API cURL Examples ==="
echo

# --- 1. Health check ---
echo "1. Health check:"
curl -s "${BASE_URL}/health" | python3 -m json.tool
echo

# --- 2. Compress text ---
echo "2. Compress text (POST /compress):"
curl -s -X POST "${BASE_URL}/compress" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The implementation of artificial intelligence systems in enterprise environments presents numerous challenges that must be carefully considered by engineering teams. Organizations frequently encounter significant difficulties related to data quality, model reliability, and seamless integration with their existing infrastructure and tooling."
  }' | python3 -m json.tool
echo

# --- 3. Compress with token target ---
echo "3. Compress to token target (POST /compress):"
curl -s -X POST "${BASE_URL}/compress" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The quarterly financial report demonstrates significant improvement across all key performance indicators. Revenue increased by approximately 23% compared to the prior year period. This exceptional growth was driven primarily by expansion in the enterprise segment, where customer acquisition accelerated meaningfully.",
    "target_tokens": 50
  }' | python3 -m json.tool
echo

# --- 4. Compress conversation history ---
echo "4. Compress conversation (POST /compress/conversation):"
curl -s -X POST "${BASE_URL}/compress/conversation" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Hi, could you please help me understand how authentication works in your system?"},
      {"role": "assistant", "content": "Of course! I would be very happy to help explain the authentication system. Our system uses JWT tokens for authentication. When you log in, you receive a signed token that you include in subsequent requests."},
      {"role": "user", "content": "That makes sense. What about token expiry? I need to understand how long tokens last."},
      {"role": "assistant", "content": "Great question! Tokens expire after 24 hours by default. You can refresh them using the /auth/refresh endpoint before they expire."}
    ],
    "target_tokens": 200
  }' | python3 -m json.tool
echo

# --- 5. Python requests equivalent ---
echo "=== Python requests equivalent ==="
cat << 'PYEOF'
import requests

BASE_URL = "http://localhost:8000"

# Compress text
response = requests.post(
    f"{BASE_URL}/compress",
    json={
        "text": "Your verbose text here that needs compression...",
        "target_tokens": 100,  # optional
    }
)
result = response.json()
print(f"Savings: {result['savings_pct']}%")
print(f"Compressed: {result['compressed']}")

# Compress conversation
response = requests.post(
    f"{BASE_URL}/compress/conversation",
    json={
        "messages": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."},
        ]
    }
)
compressed_messages = response.json()["messages"]
PYEOF

echo
echo "✅ Run 'python server.py' first, then execute this script"
