# Offline Demo

This flow needs no X, OpenAI, Telegram, or RSS network access. It analyzes the 30 synthetic posts in `data/sample_posts.json`, writes a fresh SQLite database, builds derived analytics, and serves the dashboard.

## Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:DATABASE_PATH = "data/demo.sqlite3"
$env:AI_PROVIDER = "mock"
python -m app.main --mode local --mock-ai --reset-db --summary --no-telegram
python -m app.main --graph-rebuild
python -m app.main --quality-recalculate
python -m app.main --health-report
python -m app.main --dashboard --dashboard-host 127.0.0.1 --dashboard-port 8000
```

Open `http://127.0.0.1:8000/`. The signal, narrative, token, graph, and quality pages use the same `data/demo.sqlite3` file.

## Linux Or macOS

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
export DATABASE_PATH=data/demo.sqlite3
export AI_PROVIDER=mock
python -m app.main --mode local --mock-ai --reset-db --summary --no-telegram
python -m app.main --graph-rebuild
python -m app.main --quality-recalculate
python -m app.main --dashboard --dashboard-host 127.0.0.1 --dashboard-port 8000
```

## Expected Result

- The console reports that the database reset completed and sample posts were analyzed.
- Signals and narrative history appear when the fixture crosses configured thresholds.
- `--graph-rebuild` and `--quality-recalculate` update derived tables without calling external services.
- `/live` returns a live status and `/ready` reports whether SQLite and configuration are ready.

Local sample mode exercises the post-analysis pipeline. Unified multi-source events are demonstrated separately by configuring a `local_json` source in `config/sources.json`; source orchestration is not required for the core offline smoke test.
