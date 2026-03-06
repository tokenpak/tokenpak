"""
TokenPak Self-Service Portal
-----------------------------
Manages subscriptions, license keys, and team seats.
Hosted at portal.tokenpak.dev

Routes:
  GET  /               → dashboard (requires login)
  GET  /pricing        → pricing page (public)
  POST /checkout       → create Stripe checkout session
  GET  /success        → post-payment landing
  GET  /cancel         → cancelled payment landing
  GET  /portal         → Stripe customer portal (manage/cancel sub)
  POST /webhook        → Stripe webhook handler
  GET  /keys           → list license keys
  POST /keys/regenerate → regenerate a key
  GET  /team           → team seats management
  POST /team/invite    → invite a team member

Environment variables required:
  STRIPE_SECRET_KEY      — sk_live_... or sk_test_...
  STRIPE_WEBHOOK_SECRET  — whsec_...
  STRIPE_PRO_PRICE_ID    — price_... (Pro monthly)
  STRIPE_TEAM_PRICE_ID   — price_... (Team monthly, per seat)
  TOKENPAK_ADMIN_KEY     — internal key for admin endpoints
  SECRET_KEY             — Flask session secret
  DATABASE_URL           — SQLite path (default: portal.db)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import (
    Flask, g, jsonify, redirect, render_template,
    request, session, url_for
)

# ── optional deps ─────────────────────────────────────────────────────────────
try:
    import stripe
    _STRIPE = True
except ImportError:
    stripe = None  # type: ignore
    _STRIPE = False

try:
    from tokenpak.agent.license.keys import LicenseKeyGenerator, LicensePayload
    _KEYGEN = True
except ImportError:
    _KEYGEN = False

logger = logging.getLogger(__name__)

# ── config ────────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID    = os.getenv("STRIPE_PRO_PRICE_ID", "")
STRIPE_TEAM_PRICE_ID   = os.getenv("STRIPE_TEAM_PRICE_ID", "")
DATABASE_URL           = os.getenv("DATABASE_URL", "portal.db")
SECRET_KEY             = os.getenv("SECRET_KEY", os.urandom(32).hex())
BASE_URL               = os.getenv("BASE_URL", "https://portal.tokenpak.dev")

if _STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── database ──────────────────────────────────────────────────────────────────

def _db_path() -> str:
    return DATABASE_URL.removeprefix("sqlite:///")


@contextmanager
def get_db():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS customers (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                stripe_id     TEXT,
                tier          TEXT NOT NULL DEFAULT 'oss',
                seats         INTEGER NOT NULL DEFAULT 1,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS license_keys (
                id            TEXT PRIMARY KEY,
                customer_id   TEXT NOT NULL REFERENCES customers(id),
                key_token     TEXT NOT NULL,
                tier          TEXT NOT NULL,
                seats         INTEGER NOT NULL DEFAULT 1,
                issued_at     REAL NOT NULL,
                expires_at    REAL,
                revoked       INTEGER NOT NULL DEFAULT 0,
                UNIQUE(customer_id, tier)
            );

            CREATE TABLE IF NOT EXISTS team_members (
                id            TEXT PRIMARY KEY,
                customer_id   TEXT NOT NULL REFERENCES customers(id),
                email         TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'member',
                invited_at    REAL NOT NULL,
                accepted      INTEGER NOT NULL DEFAULT 0,
                UNIQUE(customer_id, email)
            );

            CREATE TABLE IF NOT EXISTS webhook_events (
                id            TEXT PRIMARY KEY,
                event_type    TEXT NOT NULL,
                payload       TEXT NOT NULL,
                processed_at  REAL NOT NULL
            );
        """)


# ── auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "customer_id" not in session:
            return redirect(url_for("pricing"))
        return f(*args, **kwargs)
    return decorated


def _get_customer(customer_id: str) -> Optional[sqlite3.Row]:
    with get_db() as db:
        return db.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()


def _get_or_create_customer(email: str, stripe_customer_id: str = "", tier: str = "oss") -> str:
    """Return existing customer id or create new one."""
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM customers WHERE email = ?", (email,)
        ).fetchone()
        if row:
            return row["id"]
        cid = str(uuid.uuid4())
        now = time.time()
        db.execute(
            "INSERT INTO customers (id, email, stripe_id, tier, seats, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (cid, email, stripe_customer_id, tier, now, now)
        )
        return cid


# ── key helpers ───────────────────────────────────────────────────────────────

def _issue_key(customer_id: str, tier: str, seats: int = 1) -> str:
    """Generate and store a new license key. Returns the key token."""
    if _KEYGEN:
        gen = LicenseKeyGenerator()
        payload = LicensePayload(
            key_id=str(uuid.uuid4()),
            tier=tier,
            seats=seats,
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=None,
            features=_tier_features(tier),
            customer_id=hashlib.sha256(customer_id.encode()).hexdigest()[:16],
        )
        key_token = gen.generate(payload)
    else:
        # fallback: opaque random token
        key_token = f"TPAK-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"

    kid = str(uuid.uuid4())
    now = time.time()
    with get_db() as db:
        # revoke old key for this tier
        db.execute(
            "UPDATE license_keys SET revoked = 1 WHERE customer_id = ? AND tier = ? AND revoked = 0",
            (customer_id, tier)
        )
        db.execute(
            "INSERT INTO license_keys (id, customer_id, key_token, tier, seats, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kid, customer_id, key_token, tier, seats, now)
        )
    return key_token


def _tier_features(tier: str) -> list[str]:
    base = ["compression", "routing", "cost_tracking", "vault", "cli"]
    if tier in ("pro", "team", "enterprise"):
        base += ["advanced_recipes", "budget_enforcement", "priority_support"]
    if tier in ("team", "enterprise"):
        base += ["multi_agent", "shared_vault", "rbac", "audit_logs", "seat_management"]
    return base


# ── routes: public ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "customer_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("pricing"))


@app.route("/pricing")
def pricing():
    return render_template("pricing.html")


# ── routes: checkout ──────────────────────────────────────────────────────────

@app.route("/checkout", methods=["POST"])
def checkout():
    if not _STRIPE or not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe not configured"}), 503

    tier = request.form.get("tier", "pro")
    price_id = STRIPE_PRO_PRICE_ID if tier == "pro" else STRIPE_TEAM_PRICE_ID
    seats = int(request.form.get("seats", 1))

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": seats}],
            mode="subscription",
            success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/cancel",
            metadata={"tier": tier, "seats": str(seats)},
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        logger.error("Stripe checkout error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/success")
def success():
    session_id = request.args.get("session_id")
    if not session_id or not _STRIPE:
        return render_template("success.html", key=None)

    try:
        cs = stripe.checkout.Session.retrieve(session_id)
        email = cs.customer_details.email
        tier = cs.metadata.get("tier", "pro")
        seats = int(cs.metadata.get("seats", 1))

        customer_id = _get_or_create_customer(email, cs.customer, tier)
        key = _issue_key(customer_id, tier, seats)

        # update customer record
        with get_db() as db:
            db.execute(
                "UPDATE customers SET tier=?, seats=?, stripe_id=?, updated_at=? WHERE id=?",
                (tier, seats, cs.customer, time.time(), customer_id)
            )

        session["customer_id"] = customer_id
        return render_template("success.html", key=key, tier=tier, seats=seats, email=email)
    except Exception as e:
        logger.error("Success handler error: %s", e)
        return render_template("success.html", key=None, error=str(e))


@app.route("/cancel")
def cancel():
    return render_template("cancel.html")


# ── routes: Stripe customer portal ───────────────────────────────────────────

@app.route("/portal")
@login_required
def stripe_portal():
    if not _STRIPE or not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe not configured"}), 503

    customer = _get_customer(session["customer_id"])
    if not customer or not customer["stripe_id"]:
        return redirect(url_for("pricing"))

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer["stripe_id"],
            return_url=f"{BASE_URL}/dashboard",
        )
        return redirect(portal_session.url, code=303)
    except Exception as e:
        logger.error("Stripe portal error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── routes: webhook ───────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    if not _STRIPE:
        return jsonify({"error": "Stripe not configured"}), 503

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    event_id = event["id"]
    event_type = event["type"]

    # idempotency: skip if already processed
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM webhook_events WHERE id = ?", (event_id,)
        ).fetchone()
        if existing:
            return jsonify({"status": "already processed"})

        db.execute(
            "INSERT INTO webhook_events (id, event_type, payload, processed_at) VALUES (?, ?, ?, ?)",
            (event_id, event_type, json.dumps(event), time.time())
        )

    _handle_webhook_event(event)
    return jsonify({"status": "ok"})


def _handle_webhook_event(event: dict):
    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "customer.subscription.updated":
        _sync_subscription(obj)
    elif etype == "customer.subscription.deleted":
        _downgrade_customer(obj["customer"])
    elif etype in ("invoice.payment_failed", "invoice.payment_action_required"):
        logger.warning("Payment issue for customer %s", obj.get("customer"))


def _sync_subscription(sub: dict):
    stripe_customer_id = sub["customer"]
    tier = sub["metadata"].get("tier", "pro")
    seats = int(sub.get("quantity", 1))
    with get_db() as db:
        db.execute(
            "UPDATE customers SET tier=?, seats=?, updated_at=? WHERE stripe_id=?",
            (tier, seats, time.time(), stripe_customer_id)
        )


def _downgrade_customer(stripe_customer_id: str):
    with get_db() as db:
        db.execute(
            "UPDATE customers SET tier='oss', seats=1, updated_at=? WHERE stripe_id=?",
            (time.time(), stripe_customer_id)
        )


# ── routes: dashboard ────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    customer = _get_customer(session["customer_id"])
    with get_db() as db:
        keys = db.execute(
            "SELECT * FROM license_keys WHERE customer_id=? AND revoked=0 ORDER BY issued_at DESC",
            (session["customer_id"],)
        ).fetchall()
        members = db.execute(
            "SELECT * FROM team_members WHERE customer_id=?",
            (session["customer_id"],)
        ).fetchall()
    return render_template(
        "dashboard.html",
        customer=customer,
        keys=keys,
        members=members,
    )


# ── routes: keys ─────────────────────────────────────────────────────────────

@app.route("/keys")
@login_required
def list_keys():
    with get_db() as db:
        keys = db.execute(
            "SELECT * FROM license_keys WHERE customer_id=? AND revoked=0 ORDER BY issued_at DESC",
            (session["customer_id"],)
        ).fetchall()
    return render_template("keys.html", keys=keys)


@app.route("/keys/regenerate", methods=["POST"])
@login_required
def regenerate_key():
    customer = _get_customer(session["customer_id"])
    if not customer:
        return jsonify({"error": "Not found"}), 404
    key = _issue_key(session["customer_id"], customer["tier"], customer["seats"])
    return jsonify({"key": key, "tier": customer["tier"]})


@app.route("/keys/download")
@login_required
def download_keys():
    """Return all active keys as JSON (for programmatic use)."""
    with get_db() as db:
        keys = db.execute(
            "SELECT key_token, tier, seats, issued_at FROM license_keys "
            "WHERE customer_id=? AND revoked=0",
            (session["customer_id"],)
        ).fetchall()
    return jsonify([dict(k) for k in keys])


# ── routes: team ─────────────────────────────────────────────────────────────

@app.route("/team")
@login_required
def team():
    customer = _get_customer(session["customer_id"])
    if customer["tier"] not in ("team", "enterprise"):
        return render_template("upgrade.html", required_tier="team")
    with get_db() as db:
        members = db.execute(
            "SELECT * FROM team_members WHERE customer_id=? ORDER BY invited_at",
            (session["customer_id"],)
        ).fetchall()
    return render_template("team.html", customer=customer, members=members)


@app.route("/team/invite", methods=["POST"])
@login_required
def invite_member():
    customer = _get_customer(session["customer_id"])
    if customer["tier"] not in ("team", "enterprise"):
        return jsonify({"error": "Team plan required"}), 403

    email = request.form.get("email", "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Invalid email"}), 400

    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM team_members WHERE customer_id=?",
            (session["customer_id"],)
        ).fetchone()[0]
        if count >= customer["seats"]:
            return jsonify({"error": f"Seat limit reached ({customer['seats']} seats). Upgrade to add more."}), 400

        mid = str(uuid.uuid4())
        db.execute(
            "INSERT OR IGNORE INTO team_members (id, customer_id, email, role, invited_at) "
            "VALUES (?, ?, ?, 'member', ?)",
            (mid, session["customer_id"], email, time.time())
        )

    # TODO: send invite email via SendGrid/SES
    logger.info("Invited %s to team %s", email, session["customer_id"])
    return jsonify({"status": "invited", "email": email})


@app.route("/team/remove", methods=["POST"])
@login_required
def remove_member():
    email = request.form.get("email", "").strip().lower()
    with get_db() as db:
        db.execute(
            "DELETE FROM team_members WHERE customer_id=? AND email=?",
            (session["customer_id"], email)
        )
    return jsonify({"status": "removed", "email": email})


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
