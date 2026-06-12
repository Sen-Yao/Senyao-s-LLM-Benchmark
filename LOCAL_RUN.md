# Local Run

Purpose: local FastAPI + built React runtime for inspecting and running the migrated SQLite benchmark database.

## Prerequisites

- Python 3.11 with project dependencies installed. In this Codex handoff, the reusable interpreter is:

```bash
/Users/oliver/Documents/Codex/2026-06-11/new-chat-8/work/Senyao-s-LLM-Benchmark/.venv/bin/python
```

- `data/benchmark.db` is the migrated local SQLite database.
- `.env` must exist locally and must not be committed. The migrated DB in this workspace was encrypted with:

```bash
APP_SECRET_KEY=dev-local-secret
DATABASE_URL=sqlite:///./data/benchmark.db
TASKS_DIR=tasks
CORS_ORIGINS=*
```

## Start

Run from the project root:

```bash
/Users/oliver/Documents/Codex/2026-06-11/new-chat-8/work/Senyao-s-LLM-Benchmark/.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Verify

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
curl -fsS http://127.0.0.1:8000/api/health
```

Expected health response includes `{"ok":true}`.

To verify the local DB key without printing secrets:

```bash
/Users/oliver/Documents/Codex/2026-06-11/new-chat-8/work/Senyao-s-LLM-Benchmark/.venv/bin/python - <<'PY'
from backend.app.db import SessionLocal
from backend.app.models import Provider
from backend.app.security.crypto import decrypt_secret
with SessionLocal() as session:
    provider = session.query(Provider).filter(Provider.encrypted_api_key != "").first()
    assert provider is not None
    decrypt_secret(provider.encrypted_api_key)
print("decrypt-ok")
PY
```

## Notes

- If the app starts without the local `.env`, provider API keys from `data/benchmark.db` cannot be decrypted and background runs will fail immediately.
- The run page displays a model batch as waiting when the batch is accepted by the background scheduler but no task result is actively running yet.
- Last verified locally on 2026-06-12.
