# Security Policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Send a report to the maintainer via private channel. Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

## Secret management

This repo is designed to contain **no secrets**:

- All API keys, database credentials, and notify targets live in the **private overlay** (`$ORION_OVERLAY_ROOT/config/.env` and overlay YAML configs), never in this repo.
- The `config/.env` file is gitignored; only `config/.env.example` (placeholders) is committed.
- SQL dumps (`db/*.sql` except `provision.sql`) are gitignored; they live in `$ORION_OVERLAY_ROOT/db/dumps/`.
- Generated RAG indexes (`rag/chroma/`, `rag/bm25_corpus/`, `rag/index_manifest.json`) are gitignored; they live in the overlay.

## Pre-publish verification

Before any `git push`, run:

```bash
./scripts/audit-before-publish.sh
```

This scans for phone numbers, API keys (Anthropic, Stripe, AWS), production paths, and RAG artifacts. It must exit 0.

## Index-time redaction

The indexer (`rag/indexers/base.py`) applies `rag/redaction.py` to all file content before embedding. This masks:
- `whsec_*` (Stripe webhook secrets)
- `sk-ant-*`, `sk_live_*`, `sk_test_*` (Anthropic / Stripe API keys)
- `AKIA*` (AWS access key IDs)
- Common `password=`, `token=`, `secret=` assignment patterns

`.env` files (non-example) are skipped entirely.

## Key rotation

If a key was ever committed (check `git log --all -p`), rotate it immediately regardless of subsequent removal. The new key must be set in the overlay before any `bin/orion-*` call.

Known rotation events:
- **Anthropic API key**: rotate at console.anthropic.com; update overlay `.env`
- **Stripe webhook secret**: rotate in Stripe Dashboard; update application `.env`
