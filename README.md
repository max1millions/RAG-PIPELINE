# Orion RAG Pipeline

A local RAG (Retrieval-Augmented Generation) pipeline for the **Orion** OpenClaw agent. Indexes private code repositories into [Chroma](https://www.trychroma.com/) + BM25, feeds relevant chunks to a LangGraph-based code-change workflow, and provides SQL watchdog, incident supervisor, and local web smoke testing.

Originally built to operate the RightsTune music publishing platform — the framework is generic and can be adapted to any private codebase.

## Architecture

```
iMessage / CLI → Orion (Gemini)
  ├─ Run pipeline ──────► MCP server → production host      [no LLM]
  ├─ Ask about code ────► orion-rag-query                   [Gemini only]
  └─ Fix code ──────────► orion-fix → Claude + RAG chunks
```

Production data and secrets live in a **private overlay** (`ORION_OVERLAY_ROOT`), entirely separate from this repo. Clone this repo and configure the overlay to get a full operator setup, or run with only the public defaults for a reduced smoke suite.

## Two-layer model

| Layer | Location | Contains |
|-------|----------|----------|
| **Public repo** | `RAG-PIPELINE/` | Framework code, `*.example.yaml` configs, synthetic fixtures, `db/provision.sql` |
| **Private overlay** | `$ORION_OVERLAY_ROOT` (default `~/.openclaw/local/rag-pipeline/`) | Secrets, production configs, SQL dumps, RAG indexes, runtime data |

Overlay discovery order:
1. `ORION_OVERLAY_ROOT` env var
2. `paths.overlay_root` in `config/features.yaml`
3. Convention: `~/.openclaw/local/rag-pipeline/` (if the directory exists)
4. None → pure-public mode (generic defaults, no private data)

## Quick start (public, no overlay)

```bash
cd ~/.openclaw/workspace/RAG-PIPELINE

# 1. Python env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Config (copy examples; edit ANTHROPIC_API_KEY)
mkdir -p ~/.openclaw/local/rag-pipeline/config
cp config/.env.example ~/.openclaw/local/rag-pipeline/config/.env
# Edit ANTHROPIC_API_KEY and MYSQL_DATABASE in the overlay .env

# 3. Run smoke tests (no REPOS, no MySQL required)
./scripts/smoke-public.sh
```

## Operator setup (with private overlay)

See **[SETUP.md](./SETUP.md)** for full instructions including MySQL, index build, cron install.

Set `ORION_OVERLAY_ROOT` before running any `bin/orion-*` command:

```bash
export ORION_OVERLAY_ROOT=~/.openclaw/local/rag-pipeline
```

## Modules

| Module | Feature flag | CLI |
|--------|-------------|-----|
| LangGraph Planner+Coder | `features.langgraph_multiagent` | `bin/orion-code`, `bin/orion-fix` |
| RAG / Chroma | `features.rag` | `bin/orion-rag-index`, `bin/orion-rag-query`, `bin/orion-rag-eval` |
| Local MySQL | `features.local_mysql` | `bin/orion-db` |
| Incident supervisor | `features.incidents` | `bin/orion-incident` |
| SQL watchdog | `features.watchdog` | `bin/orion-watchdog` |
| Golden fixtures | (manifest) | `bin/orion-golden-test` |
| Local web | `features.local_web` | `bin/orion-web-test` |

## Configuration

| File | Purpose |
|------|---------|
| `config/features.yaml` | Feature flags, RAG limits, model names |
| `config/.env.example` | Template for secrets (copy to overlay) |
| `config/*.example.yaml` | Template for each private config (copy to overlay) |

Private configs (placed in `$ORION_OVERLAY_ROOT/config/`):

| File | Contains |
|------|---------|
| `.env` | `ANTHROPIC_API_KEY`, `MYSQL_*`, LangSmith |
| `incidents.yaml` | MCP server, notify targets, `notify_backend` |
| `path_map.yaml` | Production host path → local repo mapping |
| `watchdog.yaml` | SQL validation checks |
| `repo_tests.yaml` | Per-repo test commands |
| `web_local.yaml` | Local nginx/PHP stack settings |

## Notification backend

Set `notify_backend` in your overlay `incidents.yaml`:

- `log` — print to stdout (default; no external service required)
- `bluebubbles` — send via [OpenClaw](https://openclaw.io) + BlueBubbles / iMessage

## RAG reference

Indexes `$ORION_OVERLAY_ROOT/rag/chroma/` and `rag/bm25_corpus/`.

```bash
# First run: build index
./bin/orion-rag-index --reset --no-incremental
./bin/orion-rag-eval

# Query
./bin/orion-rag-query "entry point for CWR generation" --repo CWR-INTERFACE
./bin/orion-rag-query "database schema migration" --collection sql --json

# Code change (LangGraph + RAG + Claude)
./bin/orion-fix "Fix the ownership calculation" --repo MY-REPO
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Missing incidents config` | Copy `config/incidents.example.yaml` to overlay and configure |
| `Chroma store missing` | Run `bin/orion-rag-index` |
| `RAG disabled` | Set `features.rag: true` in `config/features.yaml` |
| `ANTHROPIC_API_KEY missing` | Set key in `$ORION_OVERLAY_ROOT/config/.env` |

## Security

See [SECURITY.md](./SECURITY.md). Run `./scripts/audit-before-publish.sh` before any push to verify no secrets are in the tree.
