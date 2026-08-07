# Docker Production Deployment

This guide deploys `x-narrative-tracker` as one hardened application container. Uvicorn serves the FastAPI dashboard while managed background threads run RSS ingestion and the optional saved-search report scheduler. Keeping these workloads in one Python process avoids independent container writers competing for SQLite. Each thread uses its own SQLite connection; WAL mode and a 30-second busy timeout handle short read/write overlap.

## Prerequisites

- Linux VPS or Docker Desktop with Docker Engine and Docker Compose v2
- Git
- Roughly 1 GB free memory and enough disk for images, SQLite, exports, and backups
- A checked-out project release or commit

Clone your repository or fork, enter the project directory, and verify Docker:

```bash
git clone <your-repository-url> x-narrative-tracker
cd x-narrative-tracker
docker --version
docker compose version
```

## Configuration

Create a production environment file and edit it locally on the server:

```bash
cp .env.production.example .env
chmod 600 .env
```

No credential is baked into the image. Compose reads `.env` at runtime. `AI_PROVIDER=mock` and blank Telegram credentials are valid: OpenAI is disabled and Telegram reports as unconfigured without blocking startup.

Important deployment values:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST_PORT` | `8000` | Host port mapped to container port 8000 |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface exposed by Compose |
| `TRACKER_ENABLED` | `true` | Runs periodic RSS ingestion alongside the dashboard |
| `FETCH_INTERVAL_SECONDS` | `900` | Delay between tracker cycles |
| `AI_PROVIDER` | `mock` | `mock`, `openai`, or `auto` |
| `LOG_FORMAT` | `json` | Container-friendly stdout logging |
| `TRACKER_SHUTDOWN_TIMEOUT_SECONDS` | `30` | Wait for an active tracker cycle during SIGTERM |
| `REPORT_SCHEDULER_ENABLED` | `true` | Checks and atomically claims due scheduled reports |
| `REPORT_OUTPUT_DIR` | `exports/scheduled_reports` | Scheduler-owned CSV retention directory |

Compose owns the container paths for the database, resources, source configuration, and bind address. Do not override `DATABASE_PATH`, `DASHBOARD_HOST`, or the bundled resource paths in normal Compose deployments.

The host `config/` directory is mounted read-only at `/app/config`. Validate `config/sources.json` before startup. Source definitions modified through the API are stored in SQLite; the mounted JSON remains the bootstrap configuration.

## Start And Verify

```bash
docker compose --env-file .env config
docker compose --env-file .env build
docker compose --env-file .env up -d
docker compose ps
docker compose logs --tail=100 app
curl --fail http://127.0.0.1:8000/live
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/metrics
```

PowerShell uses the same Compose commands and `curl.exe` for the HTTP checks.

Startup fails before Uvicorn begins accepting traffic when configuration files are missing, the data directory is not writable, SQLite cannot initialize, migrations fail, or required AI configuration is invalid. Source URLs are parsed but no network call is made during validation. The Docker healthcheck uses `/ready`, which checks critical local readiness without requiring OpenAI or Telegram.

## Container Security

The image:

- Uses the official `python:3.12-slim` base
- Runs as UID/GID `10001`, never root
- Copies only application, resource, and source configuration files
- Drops every Linux capability and enables `no-new-privileges`
- Uses a read-only root filesystem with a small `/tmp` tmpfs
- Exposes only port 8000
- Never mounts the Docker socket or embeds `.env`

The default `BIND_ADDRESS=127.0.0.1` exposes the dashboard only to the VPS itself, which is appropriate behind a reverse proxy. Use `0.0.0.0` only with an appropriate firewall and access controls.

## Persistent Storage

| Volume | Container path | Persistent state |
| --- | --- | --- |
| `tracker_data` | `/app/data` | SQLite database, AI cache, source state, rules, watchlists, graph data, quality data, signal outcomes, and observability snapshots |
| `tracker_exports` | `/app/exports` | CSV exports and SQLite backup files created inside the container |

All durable application state currently lives in SQLite. OpenAI response cache, source fetch state, saved searches, report definitions/run history, generated configuration state, and observability history are database tables. Scheduled CSV files live under `/app/exports`. Keep one scheduler-enabled container per SQLite volume; multi-container scheduling requires an external coordinator. `/tmp` and Python caches are disposable. Do not use `docker compose down --volumes` during an upgrade unless permanent data deletion is intentional.

Inspect Docker’s volume metadata:

```bash
docker volume inspect x-tracker_tracker_data
docker volume inspect x-tracker_tracker_exports
```

The exact Compose project prefix depends on the directory name or `--project-name` value.

## Logs

The application writes to stdout and stderr. No log directory is required inside the container.

```bash
docker compose logs -f app
docker compose logs --since=30m app
docker compose logs --tail=200 app
```

Use `LOG_FORMAT=json` for production log collectors. Configure Docker daemon log rotation or the host logging driver; application logs are not stored in the persistent volumes.

## SQLite Backup

Python’s SQLite backup API creates a transactionally consistent backup even while the tracker is active:

```bash
mkdir -p backups
docker compose exec -T app python -c "import sqlite3; s=sqlite3.connect('/app/data/x_narrative_tracker.sqlite3'); d=sqlite3.connect('/app/exports/x-narrative-backup.sqlite3'); s.backup(d); d.close(); s.close()"
docker compose cp app:/app/exports/x-narrative-backup.sqlite3 ./backups/x-narrative-backup.sqlite3
```

Rename host backups with a timestamp and copy them to storage outside the VPS. Test restores periodically. Do not blindly copy the live database file because WAL transactions may not yet be checkpointed.

## Restore

Restore is destructive. Verify the selected backup and create a fresh backup of current state first.

```bash
docker compose stop app
docker compose run --rm --no-deps --volume "$(pwd)/backups:/restore:ro" app python -c "from pathlib import Path; import shutil; target=Path('/app/data/x_narrative_tracker.sqlite3'); shutil.copy2('/restore/x-narrative-backup.sqlite3', target); Path(str(target)+'-wal').unlink(missing_ok=True); Path(str(target)+'-shm').unlink(missing_ok=True)"
docker compose up -d app
docker compose ps
curl --fail http://127.0.0.1:8000/ready
docker compose logs --tail=100 app
```

In PowerShell, replace `$(pwd)` with `${PWD}`. Automatic schema initialization applies forward-compatible migrations after restart.

## Update

Create a backup, then update without deleting volumes:

```bash
git fetch --tags
git pull --ff-only
docker compose --env-file .env config
docker compose --env-file .env build --pull
docker compose --env-file .env up -d
docker compose ps
docker compose logs --tail=100 app
curl --fail http://127.0.0.1:8000/ready
```

`docker compose up -d` recreates the container while retaining `tracker_data` and `tracker_exports`.

## Rollback

1. Preserve logs and back up the current database.
2. Check out the previous known-good Git tag or commit.
3. Rebuild and recreate the service without removing volumes.
4. If the release performed a database migration incompatible with the old code, stop the app and restore the matching pre-upgrade backup.
5. Verify `/ready`, `/health`, logs, and a dashboard page.

```bash
git checkout <previous-tag-or-commit>
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/ready
```

Rollback is manual; no automatic database downgrade is promised.

## Monitoring

Prometheus-compatible metrics are available at `/metrics`. No Prometheus container is required by the default deployment. Point an existing Prometheus instance at `app:8000` from a shared Docker network or at the protected host endpoint. Monitor at least:

- Container readiness and restart count
- Database size and free disk
- Source failures and fetch latency
- AI and Telegram failures
- Event Bus handler failures
- Memory, CPU, and slow-operation counters

Do not expose Prometheus or the dashboard directly to the public internet without firewall, reverse-proxy authentication, and TLS.

## Resource Guidance

A small installation can start with 1 shared CPU, 512 MB to 1 GB RAM, and 5-10 GB disk. These are planning values, not enforced Compose limits. Track real usage before setting limits. SQLite, retained article text, graph projections, CSV exports, backups, Docker JSON logs, and observability snapshots all consume disk. Keep at least twice the current database size free for migrations and backups.

SQLite is suitable for this single-instance deployment. Do not scale the app service above one replica and do not place the SQLite volume on an unreliable network filesystem. Move to a server database before introducing multiple application replicas or sustained high write concurrency.

## Troubleshooting

**Port 8000 is already in use:** set `HOST_PORT=8001` in `.env`, run `docker compose up -d`, and browse to port 8001.

**Missing `.env`:** copy `.env.production.example` to `.env`. Compose intentionally does not bake defaults containing credentials into the image.

**Database permission denied:** confirm the named volume is mounted at `/app/data`, the container runs as UID 10001, and no host bind mount with incompatible ownership replaced it. `docker compose logs app` includes the failed writable-directory check.

**Container is unhealthy:** inspect `docker compose ps`, `docker inspect`, `/ready`, and `docker compose logs --tail=200 app`. OpenAI and Telegram being disabled do not fail readiness.

**Invalid source configuration:** validate JSON syntax and required fields in `config/sources.json`, then recreate the service.

**Telegram not configured:** set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, or leave both blank. Missing optional credentials are logged and are not fatal.

**OpenAI disabled:** `AI_PROVIDER=mock` is the supported offline production mode. For OpenAI, set `AI_PROVIDER=openai`, provide `OPENAI_API_KEY`, and keep fallback enabled if desired.

**SQLite locked:** confirm there is only one app replica, no second Compose project shares the volume, and no long-running manual SQLite session is open. The application uses WAL and a 30-second busy timeout, but these cannot make multi-writer scaling safe.

**Restart loop:** run `docker compose logs app`, check `.env`, writable storage, source JSON, and database migration errors. Use `docker compose run --rm --no-deps app python -m app.main --version` to verify the image independently.

**Database volume location:** use `docker volume ls` and `docker volume inspect`. Docker Desktop stores named volumes inside its Linux VM, not as ordinary Windows folders.

**Windows Docker Desktop bind mount errors:** share the drive containing the repository, keep the project in a Docker-accessible location, and avoid OneDrive locking the mounted `config/` directory. Named data volumes avoid Windows SQLite filesystem semantics.
