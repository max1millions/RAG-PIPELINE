# Orion RAG Pipeline — Setup

First-time installation for `~/.openclaw/workspace/RAG-PIPELINE/`.

**Prerequisites:** Python 3.12+, optional local MySQL, optional LangSmith API key.

## 1. Python environment

```bash
cd ~/.openclaw/workspace/RAG-PIPELINE
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Private overlay

All secrets and production configs live outside the repo in a private overlay.

```bash
# Create overlay directory
export ORION_OVERLAY_ROOT=~/.openclaw/local/rag-pipeline
mkdir -p "$ORION_OVERLAY_ROOT/config"

# Copy and fill in each example config
cp config/.env.example          "$ORION_OVERLAY_ROOT/config/.env"
cp config/incidents.example.yaml "$ORION_OVERLAY_ROOT/config/incidents.yaml"
cp config/path_map.example.yaml  "$ORION_OVERLAY_ROOT/config/path_map.yaml"
cp config/watchdog.example.yaml  "$ORION_OVERLAY_ROOT/config/watchdog.yaml"
cp config/repo_tests.example.yaml "$ORION_OVERLAY_ROOT/config/repo_tests.yaml"
cp config/web_local.example.yaml "$ORION_OVERLAY_ROOT/config/web_local.yaml"

# Edit overlay .env — set ANTHROPIC_API_KEY and MYSQL_PASSWORD
$EDITOR "$ORION_OVERLAY_ROOT/config/.env"
```

Add to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export ORION_OVERLAY_ROOT=~/.openclaw/local/rag-pipeline
```

## 3. MySQL (optional, for watchdog and golden tests)

```bash
sudo apt install mysql-server
sudo mysql < db/provision.sql

# Import a dump into the overlay DB (dump lives in overlay):
./db/import_dump.sh "$ORION_OVERLAY_ROOT/db/dumps/your_dump.sql"

# Or stream from a remote host:
# ssh user@host 'cat /path/to/dump.sql' | ./db/import_dump.sh -
```

The DB name defaults to `orion_app` in `provision.sql`; update `MYSQL_DATABASE` in your overlay `.env` to match.

## 4. Build the RAG index

Requires REPOS checkouts at `~/.openclaw/workspace/REPOS/` (or your configured `paths.repos`).

```bash
./bin/orion-rag-index --reset --no-incremental
./bin/orion-rag-eval
```

Indexes land in `$ORION_OVERLAY_ROOT/rag/` (chroma, bm25_corpus, index_manifest.json).

## 5. Smoke tests

Minimal public suite (no MySQL or REPOS required):

```bash
./scripts/smoke-public.sh
```

Full operator suite:

```bash
./scripts/smoke-test.sh
```

## 6. Incident supervisor (requires MCP server)

Configure `$ORION_OVERLAY_ROOT/config/incidents.yaml` with your MCP server name and notify targets. Set `notify_backend: log` for stdout-only notifications (no OpenClaw required).

```bash
# Dry run (no external calls)
./bin/orion-incident poll --dry-run --json

# Live poll (MCP server must be configured and reachable)
./bin/orion-incident poll --json
```

Cron install (private workspace scripts):

```bash
~/.openclaw/workspace/scripts/install-incident-cron.sh
```

## 7. SQL watchdog

Add checks to `$ORION_OVERLAY_ROOT/config/watchdog.yaml` (copy from `config/watchdog.example.yaml`).

```bash
./bin/orion-watchdog run --dry-run --json
./bin/orion-watchdog list

# Cron install
~/.openclaw/workspace/scripts/install-watchdog-cron.sh
```

## 8. Local web stack (optional)

Configure nginx and PHP-FPM in your overlay `web_local.yaml`. Then:

```bash
~/.openclaw/workspace/scripts/boot_local.sh install-nginx
~/.openclaw/workspace/scripts/boot_local.sh start
./bin/orion-web-test
```

## Pre-publish audit

Before committing or publishing:

```bash
./scripts/audit-before-publish.sh
```

This checks for secrets, phone numbers, production paths, and RAG artifacts in the public tree. Must exit 0 before any push.
