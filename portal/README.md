# TokenPak Self-Service Portal

Web portal for managing TokenPak subscriptions, license keys, and team seats.

## Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Redirect to dashboard or pricing |
| `/pricing` | GET | Public pricing page |
| `/checkout` | POST | Create Stripe checkout session |
| `/success` | GET | Post-payment landing (shows key) |
| `/cancel` | GET | Cancelled payment |
| `/portal` | GET | Stripe customer portal (billing mgmt) |
| `/webhook` | POST | Stripe webhook handler |
| `/dashboard` | GET | Customer dashboard |
| `/keys` | GET | List license keys |
| `/keys/regenerate` | POST | Regenerate active key |
| `/keys/download` | GET | Download keys as JSON |
| `/team` | GET | Team seat management |
| `/team/invite` | POST | Invite team member |
| `/team/remove` | POST | Remove team member |

## Setup

```bash
cd portal
pip install -r requirements.txt
cp .env.example .env  # fill in Stripe keys
python app.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `STRIPE_SECRET_KEY` | Yes | Stripe secret key (sk_live_... or sk_test_...) |
| `STRIPE_WEBHOOK_SECRET` | Yes | Stripe webhook signing secret |
| `STRIPE_PRO_PRICE_ID` | Yes | Price ID for Pro plan |
| `STRIPE_TEAM_PRICE_ID` | Yes | Price ID for Team plan |
| `BASE_URL` | Yes | Portal base URL (e.g. https://portal.tokenpak.dev) |
| `SECRET_KEY` | Yes | Flask session secret |
| `DATABASE_URL` | No | SQLite path (default: portal.db) |
| `PORT` | No | HTTP port (default: 5000) |

## Deploy

```bash
# Production with gunicorn
gunicorn app:app --workers 4 --bind 0.0.0.0:5000

# Stripe webhook forwarding (dev)
stripe listen --forward-to localhost:5000/webhook
```

## Architecture

- **Flask** — lightweight web framework
- **SQLite** — local persistence (customers, keys, team members, webhook events)
- **Stripe** — subscription billing + customer portal
- **tokenpak.agent.license** — key generation (RSA-4096 signed tokens)
