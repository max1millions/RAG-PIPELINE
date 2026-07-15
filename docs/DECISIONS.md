# Design Decisions

This document explains why the pipeline is structured the way it is. For diagrams and module map, see [ARCHITECTURE.md](./ARCHITECTURE.md). For config details, see [CONFIG.md](./CONFIG.md).

## 1. Public framework + private overlay

**Problem:** The repo originally contained production SQL dumps, API keys, phone numbers, business watchdog rules, and RAG indexes that had captured secrets from source code.

**Decision:** Split into two layers:

| Layer | Location | Contains |
|-------|----------|----------|
| Public | This git repo | Framework code, `*.example.yaml` templates, synthetic fixtures |
| Private | `$ORION_OVERLAY_ROOT` (default `~/.openclaw/local/rag-pipeline/`) | Secrets, live configs, dumps, Chroma/BM25 indexes, runtime JSON |

**Why outside the repo (not `config/local/`):** `git add .` can never capture the overlay. Same trust boundary as `~/.openclaw/openclaw.json`.

**Discovery order:** `ORION_OVERLAY_ROOT` env → `paths.overlay_root` in `features.yaml` → conventional path if it exists → pure-public mode.

**Config merge:** Public `config/*.example.yaml` deep-merged with overlay YAML; overlay `.env` loaded before repo `.env` (`override=False`, overlay wins). Implemented in `common/paths.py` and `common/config.py`.

**What stays private:** SQL dumps, full watchdog rules, notify targets, path maps, production eval cases, RAG artifacts, golden CWR samples, and operator runbook (`OPS.md`). Templates in the public tree show shape only.

**Parent scripts** (`~/.openclaw/workspace/scripts/` — cron, boot) stay outside this repo but must export `ORION_OVERLAY_ROOT` and source overlay `.env`.

## 2. Security

**Index-time redaction** (`rag/redaction.py`): mask API keys, webhook secrets, and password patterns before embedding. Skip `.env` files (not `.env.example`). Artifacts live in overlay only.

**Pre-publish audit** (`scripts/audit-before-publish.sh` + CI): grep for phones, keys, Mac paths, SQL dumps, RAG artifacts. Does not flag product/repo names — those are intentional reference examples.

If secrets ever touched disk, rotate keys and re-index the overlay.

## 3. RAG

**Five Chroma collections** — `repos`, `docs`, `sql`, `playbooks`, `discrepancies` — each with a BM25 JSON sidecar. Hybrid search (vector + BM25) is off by default; enable via `rag.hybrid` or `ORION_RAG_HYBRID`.

**Query routing:** keyword heuristics pick collections (e.g. SQL terms → `sql` collection; drift language → `discrepancies`). README and discrepancy chunks get score boosts.

**Incremental indexing** via `index_manifest.json` in the overlay. Optional discrepancy scan at index time.

**Eval:** `bin/orion-rag-eval` checks recall against overlay `config/eval_cases.yaml` or in-repo `rag/eval_cases.example.yaml` (RightsTune reference cases).

## 4. Codeflow (orion-fix)

LangGraph pipeline: triage → fetch RAG context → planner (Opus) or coder (Sonnet) → apply patch → syntax check → repo tests → review → commit.

Simple tasks skip the planner. **Safety gate** before commit: max diff size and forbidden paths (`.env`, `.github/`). Plans saved under `~/.openclaw/workspace/plans/` as `REPO__slug__timestamp.md`.

## 5. Incidents and watchdog

**Reactive:** `orion-incident poll` fetches production failures via MCP.

**Proactive:** `orion-watchdog run` runs SQL checks against local MySQL.

Both share the incident FSM for dedupe and notification. **`notify_backend: log`** is the OSS default (stdout only); operators enable BlueBubbles/iMessage in overlay config.

Automation (`incidents_poll`, `auto_fix_incidents`, `watchdog_auto_fix`, etc.) defaults **off** in public `features.yaml` — enable explicitly in overlay. Watchdog Phase 4b dual-gates code (`mode: code` → RAG/`orion-fix`) or SQL (`fix_sql_file`) remediations behind `watchdog_auto_fix` plus per-check `auto_fix.enabled`; manual `orion-watchdog remediate` stays available when the parent `watchdog` feature is on.

## 6. RightsTune as portfolio deployment

Built for [RightsTune](https://rightstune.com) (CWR, CIS-Net, MUSO API, local MySQL validation). Product names in prompts, eval cases, and README examples are **intentional**, not incomplete sanitization. The repo is published as a **portfolio** of that deployment; example configs show shape for reviewers, not a third-party adoption path. See [EXAMPLES-rightstune.md](./EXAMPLES-rightstune.md).

## 7. OSS extraction (summary)

Migrated in nine additive phases: overlay plumbing → move data → split configs → remove hardcoded fallbacks → drop SQL dump from public tree → wire parent scripts → audit + redaction → docs → verification. Clean first commit (no history rewrite); keys rotated where secrets had been on disk.

## Testing

| Audience | Script |
|----------|--------|
| Public clone / CI | `./scripts/smoke-public.sh` + `./scripts/audit-before-publish.sh` |
| Operator with overlay | `./scripts/smoke-test.sh` |

Public smoke verifies imports and CLI wiring only — not full RAG indexing (requires REPOS + overlay).
