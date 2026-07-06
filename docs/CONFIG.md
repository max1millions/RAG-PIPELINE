# Configuration Reference

How configuration is loaded and merged. Background: [DECISIONS.md §1](./DECISIONS.md#1-public-framework--private-overlay).

## Merge order

1. Public `config/{name}.example.yaml` (or live `{name}.yaml` if present)
2. Overlay `$ORION_OVERLAY_ROOT/config/{name}.yaml` — deep-merge, overlay wins
3. `.env`: overlay first, then repo; `load_dotenv(override=False)`

`features.yaml` merges the same way via `common/config.py`.

## Config file map

| Public template | Overlay path | Consumer |
|-----------------|--------------|----------|
| `config/features.yaml` | `config/features.yaml` | All modules |
| `config/.env.example` | `config/.env` | Secrets, MySQL |
| `config/incidents.example.yaml` | `config/incidents.yaml` | Incidents, notify |
| `config/path_map.example.yaml` | `config/path_map.yaml` | MCP ingest |
| `config/watchdog.example.yaml` | `config/watchdog.yaml` | SQL checks |
| `config/repo_tests.example.yaml` | `config/repo_tests.yaml` | orion-fix tests |
| `config/web_local.example.yaml` | `config/web_local.yaml` | orion-web-test |
| `rag/eval_cases.example.yaml` | `config/eval_cases.yaml` | orion-rag-eval |

`config/eval_cases.example.yaml` is a generic pointer only — `rag/eval.py` loads from `rag/eval_cases.example.yaml` when no overlay file exists.

## RAG auto-reindex flags

| Flag | Default | Purpose |
|------|---------|---------|
| `rag.index_on_fix` | `true` | Reindex the touched repo after `orion-fix` commits (`codeflow/fix.py`) |
| `rag.index_on_fix_collections` | `[repos, docs]` | Which collections to reindex on fix — keep small, `sql`/`playbooks`/`discrepancies` rarely change from a code fix |

Post-merge reindexing (after `git pull` in `REPOS/*`) is not a `features.yaml` flag — it is a `post-merge` git hook installed per-repo via `scripts/install-rag-reindex-hook.sh`.

Deploy reindexing (after push to `main` on GitHub) is wired in `~/.openclaw/deploy-scripts/deploy-sync.sh` — called by every `deploy-orion-node.yml` workflow on the self-hosted runner. Best-effort background job; deploy succeeds even if reindex fails.

See [README.md](../README.md#automatic-reindexing).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ORION_OVERLAY_ROOT` | Overlay directory |
| `ORION_RAG_HYBRID` | Force BM25+vector hybrid |
| `ANTHROPIC_API_KEY` | Claude (overlay `.env`) |
| `MYSQL_*` | Local MySQL |
| `OPENCLAW_TOKEN` | iMessage notify (cron) |

`repo_tests.yaml` supports `${STACK_ROOT}` for portable test command paths.

## Operator scripts (not in this repo)

Cron/boot scripts under `~/.openclaw/workspace/scripts/` should export `ORION_OVERLAY_ROOT`, source overlay `.env`, and preserve env when calling `bin/orion-*`. Details in overlay `OPS.md`.

## Related

- [ARCHITECTURE.md](./ARCHITECTURE.md)
