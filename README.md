# Belancer Game backend

Use Python 3.12 for the tested setup. In PyCharm create/select a Python 3.12 virtual environment for this repository. The root `main.py` is the IDE sample; the API entry point is `app.main:app`.

With the virtual environment activated, run from the repository root:

```powershell
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API health: http://localhost:8000/api/health
API documentation: http://localhost:8000/docs
Frontend: http://localhost:5173

SQLite is the default local database. Run migrations before starting the API. PostgreSQL is required for realistic concurrent competition testing.

To use environment settings, copy `.env.example` to `.env` and launch with:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
```

Alembic does not load `.env` automatically. Set DATABASE_URL in the terminal when migrating a custom database:

```powershell
$env:DATABASE_URL = 'your_database_connection_string'
python -m alembic upgrade head
```

Tests:

```powershell
python -m pytest tests -q
```

The psycopg binary pin has been updated from 3.2.6 to 3.2.10, which publishes Windows CPython 3.14 wheels. The rest of this project's pinned dependency set is tested on Python 3.12; use that interpreter if another package fails on a newer Python version.

Do not commit `.env`, passwords, databases or virtual environments. See `docs/STEP_2.md` for version 2 rules and API changes. Competition-integrity hardening and full admin management remain before production.
