# Belancer Game · Backend

**A FastAPI game-platform API with server-authoritative ranked scoring, account management, audited administration and premium-access infrastructure.**

[Frontend repository](https://github.com/delowarai/belancer-game-frontend) · [Playable portfolio demo](https://belancer-game.netlify.app) · [Ranked rules](docs/STEP_2.md) · [Leaderboard design](docs/STEP_5.md)

> The public Netlify site runs free browser-only practice games. This repository powers the separate backend-connected application; accounts, rankings, admin services and payments are not active on that public demo.

## Project overview

Belancer Game combines a 36-game frontend with a backend that manages the parts requiring shared state and trusted decisions. Five games support ranked challenges: **Memory Grid, Pair Finder, Quick Match, Focus Finder and Math Sprint**. Other games provide local practice experiences, with account saves available for supported campaigns in the full application.

The backend does more than store a score submitted by a browser. It issues ranked sessions, validates gameplay actions against server rules, computes results and maintains game-specific standings. Administrative changes and payment access also pass through server-side authorization.

| Area | Implementation |
| --- | --- |
| API framework | FastAPI, Pydantic and Uvicorn |
| Persistence | SQLAlchemy with Alembic migrations |
| Databases | SQLite for local development; PostgreSQL support through psycopg |
| Authentication | Password hashing, session cookies and account lifecycle controls |
| Competition | Versioned ranked sessions, frozen challenge settings and per-game standings |
| Administration | Role-protected management, moderation and audit records |
| Product features | Catalog policies, supported campaign saves and entitlements |
| Payments | Stripe and SSLCommerz integration with verified notifications |
| Validation | pytest and HTTPX/TestClient regression tests |

## What I implemented

- Registration, login/logout, current-account lookup, username/password updates and sign-out across sessions.
- Daily challenges with server-controlled deadlines, attempt limits and versioned rule configuration.
- Action validation and authoritative score calculation for the five ranked games.
- Daily, weekly and all-time standings separated by game and frozen configuration profile.
- Personal progress, ranked activity and account-isolated practice saves for supported campaigns.
- Administrative game settings, user roles, suspension, challenge scheduling, result review and public-content editing.
- Audited access policies, premium entitlements and payment-provider integration.
- Database migrations and regression tests for authorization, gameplay, rankings and product flows.

## How ranked play works

```text
Player requests a challenge
          |
          v
Server checks account, availability, access and attempt limit
          |
          v
Server issues a session with fixed rules and a deadline
          |
          v
Browser sends ordered gameplay actions
          |
          v
Server validates actions and computes the result
          |
          v
Eligible results enter that game's configuration-specific standings
```

**The browser is not the scoring authority.** Local practice results and cosmetic coins do not become ranked scores. Ranked settings are versioned, and published challenge configuration remains fixed so later administrative edits do not rewrite an existing competition.

Started and interrupted sessions count toward the daily attempt limit. Competition dates use **UTC**. Moderation can flag or invalidate results while preserving the underlying score and review history.

## API areas

The interactive OpenAPI documentation at `/docs` is the source of truth for request schemas.

| Area | Representative routes | Purpose |
| --- | --- | --- |
| Health | `GET /api/health` | Service readiness check |
| Authentication | `/api/auth/register`, `/login`, `/logout`, `/me` | Account sessions |
| Catalog | `GET /api/games`, `/api/content`, `/api/challenges` | Games, public content and daily lineup |
| Ranked sessions | `/api/sessions`, `/{sid}/actions`, `/{sid}/finish` | Start, validate and finish attempts |
| Standings | `GET /api/standings/{game}` | Period-specific rankings |
| Account | `/api/account/*`, `/api/progress` | Profile updates and personal progress |
| Practice | `/api/practice/saves/{game}`, `/api/practice/history` | Account-isolated saves and recorded activity |
| Administration | `/api/admin/*` | Role-protected platform management |
| Billing | `/api/billing/*` | Checkout and provider notification handling |

## Local setup

Use **Python 3.12**, the tested interpreter for this dependency set. Run commands from this repository's root.

### 1. Create an environment and install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy the example using `cp .env.example .env`.

### 2. Apply database migrations

```powershell
python -m alembic upgrade head
```

SQLite is the default local database. Alembic does not automatically load `.env`; set `DATABASE_URL` in the terminal before migrating a custom database:

```powershell
$env:DATABASE_URL = 'your_database_connection_string'
python -m alembic upgrade head
```

### 3. Start the API

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
```

| Local service | URL |
| --- | --- |
| Health check | http://localhost:8000/api/health |
| Swagger UI | http://localhost:8000/docs |
| Frontend | http://localhost:5176 |

The API entry point is **`app.main:app`**. The root `main.py` is an IDE sample, not the application server.

Start the frontend separately following its README. Vite proxies `/api` to the backend, keeping browser requests on the frontend origin. The default local configuration accepts `localhost:5176` and `127.0.0.1:5176`; use one hostname consistently because cookies are hostname-specific. A configured production origin remains exact.

## Configuration

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Database connection string |
| `PUBLIC_ORIGIN` | Allowed frontend origin |
| `COOKIE_SECURE` | Secure-cookie behavior; enable for HTTPS hosting |
| `PAYMENT_PUBLIC_ORIGIN` | Public HTTPS origin used by payment flows |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Stripe server credentials |
| `SSLCOMMERZ_STORE_ID`, `SSLCOMMERZ_STORE_PASSWORD` | SSLCommerz server credentials |
| `SSLCOMMERZ_SANDBOX` | SSLCommerz test/live mode |

Payment providers remain disabled until credentials and administrative settings are configured. Keep secrets in environment variables. Never commit `.env`, credentials, local databases or virtual environments.

## Administration

There are no default admin credentials. Register an account, then explicitly promote it using the operator command:

```powershell
python -m app.manage promote YOUR_USERNAME
```

Replace `YOUR_USERNAME` with an existing active account. Refresh the frontend and open `/admin`.

Administrative mutations require a reason and create an audit record. Role-management safeguards prevent self-suspension/self-demotion and removal of the last active administrator. Suspending an account revokes its sessions and excludes it from rankings.

See [Administration design](docs/STEP_3.md) and [Account/progress features](docs/STEP_4.md).

## Payments and access

The full application includes hosted checkout integration for **Stripe** and **SSLCommerz**, with entitlements managed by the backend. Checkout prices come from server-side policies, not browser input. A success-page redirect does not grant access: verified provider notifications determine payment completion.

These integrations are not live on the portfolio demo. Real merchant setup, credentials, operational refund handling and deployment checks are separate release tasks. Browser-local game coins have no monetary value.

## Tests

```powershell
python -m pytest tests -q
```

Tests cover authentication, administrator permissions, ranked actions and deadlines, challenge configuration, moderation, standings, account isolation, premium access and payment-notification verification. The latest local backend validation completed with **85 passing tests**.

SQLite tests are useful for regression checks; realistic concurrent competition testing should also use PostgreSQL. Passing the suite does not imply a production security audit or completed live-payment certification.

## Repository guide

```text
app/
  main.py           FastAPI application, core models and account/session routes
  ranked.py         Ranked action validation and session lifecycle
  standings.py      Period and configuration-specific rankings
  account.py        Account settings and personal progress
  admin.py          Authorized management and audit workflows
  product.py        Access policies, entitlements and practice saves
  billing.py        Checkout and payment verification
  catalog_data.py   Shared game catalog
  manage.py         Explicit administrator provisioning command
tests/              API and domain regression tests
docs/               Design notes and feature milestones
alembic.ini         Database migration configuration
requirements.txt    Pinned Python dependencies
```

## Current scope

This is a portfolio backend with implemented and tested platform flows, not a claim of a finished production service. The public playable demo intentionally runs without it. Production deployment needs persistent storage, HTTPS, correct cookie/origin configuration and operational monitoring. Email recovery, live payment operations and additional production hardening remain future work.

Older setup notes are preserved in [Implementation history](docs/IMPLEMENTATION_HISTORY.md).
