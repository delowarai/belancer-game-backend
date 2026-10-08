# Step 3 — administration

Upgrade both repositories and run `python -m alembic upgrade head` from the backend root. Revision 0003 adds account active state, challenge lifecycle status, game settings, public content and audit tables. Existing accounts stay active; existing daily challenges stay published. No existing users, attempts or results are deleted.

## First admin

Sign up normally, then use the PyCharm backend terminal with the virtual environment active:

```powershell
python -m app.manage promote YOUR_USERNAME
```

Replace YOUR_USERNAME with the account you created. This requires operator access to the configured database. The command reads `.env` if present and does not overwrite existing environment variables. Promotion is logged. There is no default admin password, public first-admin endpoint or automatic admin on registration.

Refresh the browser and open `/admin`. Further admins can be assigned through Users by an existing admin. Admins cannot suspend/demote themselves, and the API preserves at least one active admin. Anyone given the admin role can manage users, rankings and content; grant it only to trusted operators.

## Available panels

- Overview: users, sessions, result statuses, challenge and audit counts.
- Users: search and pagination; active/suspended state; player/admin roles. Suspension revokes sessions and excludes the player from rankings. Reactivation does not restore old login tokens.
- Games: names, descriptions, additional instructions, availability and supported duration/round/cell defaults. Settings persist in the database and appear in the player catalog. Disabling a game stops new starts; already-started attempts can finish.
- Challenges: create a UTC dated draft for today or the next year; publish or close it. Configuration, content and additional instructions are captured at draft creation. Editing game defaults does not change existing challenges. Published challenges cannot become draft; closed challenges cannot reopen. Closure blocks new starts while existing sessions continue under their issued rules. Daily challenge generation on first play is still the default if no draft exists.
- Results: search/status filters, pagination and recorded event inspection; flag/clear, invalidate/restore with required reasons. Invalidation excludes an attempt from ranking and the next best valid attempt can replace it. Original scores remain unchanged; actions are audited. Manual flags do not themselves remove an attempt.
- Content: homepage headline/description, FAQ entries and contact copy, rendered as plain text.
- Audit: paginated actor, target, before/after and reason history. No HTTP endpoint deletes or edits audit records.

All administrative API reads/mutations require an active admin login. CSRF protections still apply. Every successful change records the acting username and reason. Password hashes, login tokens and the unrevealed puzzle are not included in user/result list responses.

## Supported configuration

Durations: 10–600 seconds. Memory: 1–10 rounds and 3–8 cells, with at least 2.1 seconds per round. Focus: 1–50 rounds. Math/Quick Match: 1–120 questions. Pair Finder keeps eight pairs. New mechanics still require code changes. Practice continues to use its documented fixed/adaptive rules independently of ranked configuration.

## Remaining production work

Only player/admin roles are provided; scoped reviewer/content-editor roles are deferred. Result status remains in legacy text JSON, so admin result filtering and overview validation counts scan stored results; a normalized indexed results table is needed at larger scale. Audit records are append-only through the API, not tamper-proof against database operators. Automatic suspicious-result detection, strict PostgreSQL load/concurrency verification, email recovery, full progress analytics, uploadable thumbnails, HTTPS deployment, monitoring and backups remain.
