# Architecture Overview

Orion RAG Pipeline is a local operator stack for the **Orion** OpenClaw agent: RAG indexing and retrieval, LangGraph-based code repair, SQL watchdog, incident supervision, and web smoke testing. Production secrets and operator data live in a **private overlay**; this repo ships the reusable framework.

Design rationale: [DECISIONS.md](./DECISIONS.md). RightsTune reference: [EXAMPLES-rightstune.md](./EXAMPLES-rightstune.md).

## System diagram

```mermaid
flowchart TB
    subgraph agent [OrionAgent]
        CLI[iMessage_or_CLI]
        Gemini[Gemini_supervisor]
    end
    subgraph pipeline [RAG_PIPELINE_public]
        RAG[orion_rag_query_index]
        Fix[orion_fix_LangGraph]
        Inc[orion_incident]
        WD[orion_watchdog]
        Web[orion_web_test]
    end
    subgraph overlay [PrivateOverlay_ORION_OVERLAY_ROOT]
        Secrets[config_dot_env]
        Configs[watchdog_path_map_incidents]
        Artifacts[rag_chroma_bm25_data]
    end
    CLI --> Gemini
    Gemini --> RAG
    Gemini --> Fix
    Gemini --> Inc
    Inc --> MCP[MCP_production_host]
    WD --> MySQL[(local_MySQL)]
    RAG --> Artifacts
    Fix --> RAG
    pipeline --> overlay
```

## Two-layer model

```mermaid
flowchart LR
    pub["Public: config/*.example.yaml + framework code"] --> merge{"deep_merge"}
    ovl["Overlay: ORION_OVERLAY_ROOT/config/*.yaml"] --> merge
    merge --> eff["Effective config"]
```

Overlay discovery: `ORION_OVERLAY_ROOT` → `features.yaml` `paths.overlay_root` → `~/.openclaw/local/rag-pipeline/` if present → pure-public mode.

## CLI modules

| CLI | Module | Feature flag | Purpose |
|-----|--------|--------------|---------|
| `bin/orion-rag-index` | `rag/` | `features.rag` | Build Chroma + BM25 indexes |
| `bin/orion-rag-query` | `rag/` | `features.rag` | Retrieval for questions |
| `bin/orion-rag-eval` | `rag/` | `features.rag` | Recall evaluation |
| `bin/orion-fix` | `codeflow/` | `features.langgraph_multiagent` | LangGraph auto-fix with RAG |
| `bin/orion-code` | `codeflow/` | `features.langgraph_multiagent` | Code worker entry |
| `bin/orion-db` | `db/` | `features.local_mysql` | Local MySQL queries |
| `bin/orion-incident` | `incidents/` | `features.incidents` | MCP poll, remediate, notify |
| `bin/orion-watchdog` | `watchdog/` | `features.watchdog` | SQL validation checks |
| `bin/orion-golden-test` | `golden/` | (manifest) | Fixture regression tests |
| `bin/orion-web-test` | `web/` | `features.local_web` | Playwright smoke pages |

## Data flows

### Indexing

REPOS and docs → indexers → redaction → Chroma + BM25 sidecar + manifest → overlay `rag/`.

### Query

Question → intent heuristics → collection routing → vector search (optional BM25 hybrid) → ranked chunks.

### Auto-fix

```mermaid
flowchart LR
    Start --> Triage --> FetchRAG --> PlannerOrCoder{complexity}
    PlannerOrCoder -->|complex| Planner --> Coder
    PlannerOrCoder -->|simple| Coder
    Coder --> Apply --> Syntax --> TestRun --> Review
    Review -->|approved| Commit
    Review -->|retry| Coder
```

### Monitoring

| Path | CLI | Source |
|------|-----|--------|
| Reactive | `orion-incident poll` | MCP production host |
| Proactive | `orion-watchdog run` | Local MySQL |

## Configuration

Layered merge and file map: [CONFIG.md](./CONFIG.md). Feature flags in `config/features.yaml`.

## Code entry points

- `common/paths.py` — overlay and path resolution
- `common/config.py` — features and `.env`
- `rag/retrieve.py` — retrieval API
- `codeflow/graph.py` — LangGraph workflow
- `incidents/fsm.py` — incident state
- `watchdog/run.py` — watchdog runner
