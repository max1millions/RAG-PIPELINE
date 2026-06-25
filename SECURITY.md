# Security Policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Report via **GitHub Security Advisories** (Private vulnerability reporting) on the repository, or contact the maintainer through your established private channel. Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact

## Secret management

This repo is designed to contain **no secrets**:

- All API keys, database credentials, and notify targets live in the **private overlay** (`$ORION_OVERLAY_ROOT/config/.env` and overlay YAML configs), never in this repo.
- The `config/.env` file is gitignored; only `config/.env.example` (placeholders) is committed.
- SQL dumps (`db/*.sql` except `provision.sql`) are gitignored; they live in `$ORION_OVERLAY_ROOT/db/dumps/`.
- Generated RAG indexes (`rag/chroma/`, `rag/bm25_corpus/`, `rag/index_manifest.json`) are gitignored; they live in the overlay.

See [docs/DECISIONS.md](./docs/DECISIONS.md) §1–2.

## Pre-publish verification

Before any `git push`, run:

```bash
./scripts/audit-before-publish.sh
```

This scans for phone numbers, API keys (Anthropic, Stripe, AWS), production paths (`/Users/`), and RAG artifacts. It must exit 0.

**What the audit does not check:** product or repo names — allowed as reference examples.

## Index-time redaction

The indexer (`rag/indexers/base.py`) applies `rag/redaction.py` before embedding. See [docs/DECISIONS.md §2](./docs/DECISIONS.md#2-security).

Masked patterns include:

- `whsec_*` (Stripe webhook secrets)
- `sk-ant-*`, `sk_live_*`, `sk_test_*` (Anthropic / Stripe API keys)
- `AKIA*` (AWS access key IDs)
- Common `password=`, `token=`, `secret=` assignment patterns
- MLC/iCloud app-password format (`xxxx-xxxx-xxxx-xxxx`)

`.env` files (non-example) are skipped entirely.

### Redaction limitations

- **Regex-based** — novel secret formats may not match until rules are updated.
- **False positives** — documentation discussing secret *shapes* may be partially masked.
- **False negatives** — non-standard encoding or split secrets may slip through; gitignore and audit remain required.
- **Placeholder env files** (`.env.example`) are indexed; they contain no real secrets by convention.

Re-index the overlay after changing redaction rules.

## Key rotation

If a key was ever committed (check `git log --all -p`), rotate it immediately regardless of subsequent removal. The new key must be set in the overlay before any `bin/orion-*` call.

Known rotation events:

- **Anthropic API key**: rotate at console.anthropic.com; update overlay `.env`
- **Stripe webhook secret**: rotate in Stripe Dashboard; update application `.env`

If secrets were ever embedded in RAG indexes, delete overlay `rag/` artifacts and re-run `orion-rag-index --reset`.
