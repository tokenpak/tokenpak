# TokenPak Self-Service Portal

Web portal for managing TokenPak license keys and team seats.

## Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Redirect to dashboard |
| `/dashboard` | GET | Dashboard |
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
cp .env.example .env  # fill in config
python app.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BASE_URL` | Yes | Portal base URL (e.g. https://portal.tokenpak.dev) |
| `SECRET_KEY` | Yes | Flask session secret |
| `DATABASE_URL` | No | SQLite path (default: portal.db) |
| `PORT` | No | HTTP port (default: 5000) |

## Deploy

```bash
# Production with gunicorn
gunicorn app:app --workers 4 --bind 0.0.0.0:5000
```

## Architecture

- **Flask** — lightweight web framework
- **SQLite** — local persistence (users, keys, team members)
- **tokenpak.agent.license** — key generation (RSA-4096 signed tokens)
