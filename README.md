# Orion Stack

RAG pipeline module for **Orion**, the OpenClaw agent that operates the [RightsTune](https://rightstune.com) music publishing administration platform.

**Public portfolio.** This repo is published so reviewers can see how Orion is built and operated for RightsTune. It is not a starter kit or drop-in overlay for other projects — `*.example.yaml` files and deep-research docs illustrate the real deployment’s shape, not a productized install path.

## Documentation


| Doc                                                              | Contents                                            |
| ---------------------------------------------------------------- | --------------------------------------------------- |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)                   | System overview and diagrams                        |
| [docs/DECISIONS.md](./docs/DECISIONS.md)                         | Design rationale (overlay, RAG, codeflow, security) |
| [docs/EXAMPLES-rightstune.md](./docs/EXAMPLES-rightstune.md)     | RightsTune deployment context (portfolio)           |
| [docs/CONFIG.md](./docs/CONFIG.md)                               | Config merge order and file map                     |
| [docs/Deep Research Reports/](./docs/Deep%20Research%20Reports/) | Architecture research behind agentic ops & testing  |


## What this is

RightsTune runs a large set of Python, PHP, and SQL pipelines that handle music publishing administration — registering works with societies worldwide, generating **CWR** (Common Works Registration) files, reconciling **ISWC** and **IPI** identifiers via the ISWC Resolution Service and IPI Context API, ingesting royalty statements, validating catalog data, operating the client portal at rightstune.com, & much more.

**Orion** is the OpenClaw agent that I can talk to via iMessage that runs and maintains those pipelines. It uses custom built MCP tools to call production code, and delegates code edits to `**orion-fix`**, a subprocess that calls **Claude** (Sonnet/Opus via LangGraph). Opus creates the plan documents, then Sonnet actually implements the changes.

Orion uses this **RAG pipeline** to pull small, relevant chunks from a local Chroma index instead of loading whole repos into model context. Indexing and search are local (embeddings only, no LLM on the retrieval path). This is so Orion can understand the codebase well enough to provide context to Claude when making code changes autonomously.

**In short:** this module gives Orion a searchable memory of the entire RightsTune source code, READMEs, SQL scripts, docs, etc. so it can answer questions and ship fixes grounded in how the system actually works. Orion pushes code changes to a separate branch in each repo so I can review and merge via human-in-the-loop protocols (HITL).

Orion uses RAG in two ways: `**orion-rag-query**` for exploration and answers, and `**orion-fix**` which injects RAG context into the LangGraph code-change workflow.

## Architecture

Orion runs in an **isolated local Linux instance** on a Mac Mini. Production pipelines run on the **Mac host**, reached over Tailscale via MCP.

```
Mac Mini
├── Linux (Orion)          OpenClaw - local test MySQL & dummy .env variables
└── macOS (production)     rightstune MCP, real API keys & production .env variables
         ▲
         └── Orion calls MCP tools in Runtime Mode; Python runs here, not in Linux
```

```
iMessage → Orion
  ├─ Run pipeline ──────► MCP rightstune → Mac production     [no Claude]
  ├─ Ask about code ────► orion-rag-query                     [Gemini only]
  └─ Fix REPOS code ────► orion-rag-query → orion-fix → Claude + RAG chunks
```

Orion also supports multimodal iMessage (photos, voice memos), self-healing automated alerts from `orion-incident` / `orion-watchdog` — *autonomous agent loops* for production failures and local SQL anomalies.

### Local proxy `.env` (Linux)

When Orion runs Python code locally to run backtests in any repository when modifying code, the openclaw agent cannot read secure credentials to enforce the principle of least privilege. It can only read dummy variables injected by proxy scripts.

Default production execution only uses **MCP** `rightstune` on the Mac when I make natural language commands (e.g. "Download and process acknowledgements"). The proxy credentials are for optional local runs for testing by the agent without copying secrets to Linux. When I say something like "Let's modify the code..." Orion routes requests through the RAG pipeline (and ultimately delegates code changes to Claude subagents).

## Private overlay (secrets & production data)

Production secrets, SQL dumps, RAG indexes, and operator-specific configs live **outside this repo** in a private overlay. The public tree ships framework code and `*.example.yaml` **illustrations** of that overlay’s shape — not configs meant for third parties to copy into their own stacks.


| Layer               | Location                                                          | Contains                                                                         |
| ------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **Public repo**     | `RAG-PIPELINE/`                                                   | Framework code, example configs, synthetic fixtures, `db/provision.sql`          |
| **Private overlay** | `$ORION_OVERLAY_ROOT` (default `~/.openclaw/local/rag-pipeline/`) | `.env`, incidents/watchdog configs, SQL dumps, Chroma/BM25 indexes, runtime JSON |


## RAG reference

Indexes `REPOS/` into Chroma (`$ORION_OVERLAY_ROOT/rag/chroma/` when overlay is configured). Retrieval is **local and LLM-free**; Claude is only used inside `orion-fix` for edits.

### Collections


| Key             | Source                                         | Notes                                     |
| --------------- | ---------------------------------------------- | ----------------------------------------- |
| `repos`         | Code under each REPOS subdir                   | Excludes `SQL-SCRIPTS/` tree              |
| `docs`          | README, CONTRIBUTING, AGENTS, TOOLS (depth ≤2) | README boosted at query time              |
| `sql`           | `SQL-SCRIPTS/**/*.sql`                         | Auto-included for SQL/schema queries      |
| `playbooks`     | Resolved incidents                             | From overlay `data/incidents/active.json` |
| `discrepancies` | Doc/code drift scan                            | README paths, env mismatches              |


### Common commands

```bash
# Index (first run: add --reset --no-incremental)
./bin/orion-rag-index
./bin/orion-rag-eval

# Query
./bin/orion-rag-query "CWR ack processor" --repo CWR-INTERFACE
./bin/orion-rag-query "duplicate writers" --collection sql --json

# Code change (Orion invokes this; uses Claude + RAG internally)
./bin/orion-fix "Fix society code mapping" --repo CWR-INTERFACE --json
```

Intents: `general` (default), `discrepancy`, `incident`. Optional `--hybrid` for BM25+vector fusion. Python API: `rag.retrieve.retrieve()`.

## Modules


| Module                  | Flag                            | CLI                                                                |
| ----------------------- | ------------------------------- | ------------------------------------------------------------------ |
| LangGraph Planner+Coder | `features.langgraph_multiagent` | `bin/orion-code`, `bin/orion-fix`                                  |
| RAG / Chroma            | `features.rag`                  | `bin/orion-rag-index`, `bin/orion-rag-query`, `bin/orion-rag-eval` |
| Local MySQL             | `features.local_mysql`          | `bin/orion-db`                                                     |
| Incident supervisor     | `features.incidents`            | `bin/orion-incident`                                               |
| Local watchdog          | `features.watchdog`             | `bin/orion-watchdog`                                               |
| Golden fixtures         | (manifest)                      | `bin/orion-golden-test`                                            |
| Local web               | `features.local_web`            | `bin/orion-web-test`                                               |


## Configuration


| File                    | Purpose                                                         |
| ----------------------- | --------------------------------------------------------------- |
| `config/features.yaml`  | Feature flags, RAG limits, Claude model names                   |
| `config/.env.example`   | Illustrative secrets shape (live copy in private overlay)       |
| `config/*.example.yaml` | Illustrative incidents, watchdog, path_map, etc. (overlay only) |


Key flags: `features.rag`, `rag.hybrid`, `rag.incremental`, `limits.rag_top_k`, `limits.eval_recall_threshold`. Paths: `paths.repos` → `~/.openclaw/workspace/REPOS`.

Incident notify backend (`incidents.yaml` in overlay): `log` (stdout only) or `bluebubbles` (OpenClaw iMessage).

## Troubleshooting


| Issue                       | Fix                                                        |
| --------------------------- | ---------------------------------------------------------- |
| `Missing incidents config`  | Copy `config/incidents.example.yaml` to overlay            |
| Chroma store missing        | `./bin/orion-rag-index`                                    |
| RAG disabled                | `features.rag: true` in `features.yaml`                    |
| Eval fails                  | Re-index with `--reset`, or tune overlay `eval_cases.yaml` |
| Watchdog false positives    | Refresh local DB: `./db/import_dump.sh ...`                |
| `ANTHROPIC_API_KEY missing` | Set key in `$ORION_OVERLAY_ROOT/config/.env`               |


## License

This repository is public for portfolio and review purposes. All rights reserved unless otherwise stated.

## Related docs

**In this repo (`docs/`):** [ARCHITECTURE](./docs/ARCHITECTURE.md) · [DECISIONS](./docs/DECISIONS.md) · [RightsTune example](./docs/EXAMPLES-rightstune.md)

**Workspace skills:**

- `../skills/RAG-SEARCH_SKILL.md` — dev-mode RAG workflow before `orion-fix`
- `../skills/ORION-DEV_SKILL.md` — code modification protocol
- `../skills/GIT-SYNC_SKILL.md` — `orion` branch push rules
- `../skills/OPENCLAW-PROXY_SKILL.md` — proxy runtime when running REPOS Python locally
- `../DOCUMENTATION/Phase4-Watchdog-Golden.md` — watchdog + golden tests

