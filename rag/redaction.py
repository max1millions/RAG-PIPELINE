"""Secret redaction for RAG indexing.

Applied to file content before chunking and before writing to the BM25 corpus,
so that secrets from REPOS source files do not land in index artifacts.
"""
from __future__ import annotations

import re

# Patterns to redact from indexed text. Each tuple is (pattern, replacement).
_REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    # Anthropic API keys
    (re.compile(r"sk-ant-api0[0-9]-[A-Za-z0-9_\-]{10,}"), "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI API keys
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED_OPENAI_KEY]"),
    # Stripe live secret keys
    (re.compile(r"sk_live_[A-Za-z0-9]{10,}"), "[REDACTED_STRIPE_LIVE_KEY]"),
    # Stripe test secret keys (also redact; test keys still grant API access)
    (re.compile(r"sk_test_[A-Za-z0-9]{10,}"), "[REDACTED_STRIPE_TEST_KEY]"),
    # Stripe webhook secrets
    (re.compile(r"whsec_[A-Za-z0-9]{10,}"), "[REDACTED_STRIPE_WEBHOOK]"),
    # Stripe publishable keys
    (re.compile(r"pk_(live|test)_[A-Za-z0-9]{10,}"), "[REDACTED_STRIPE_PK]"),
    # AWS access key IDs
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY_ID]"),
    # AWS secret access keys (common assignment patterns)
    (re.compile(
        r"(?i)(aws_secret_access_key|aws_secret_key)\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"
    ), r"\1=[REDACTED_AWS_SECRET]"),
    # Generic passwords in env-var / YAML assignment forms
    (re.compile(
        r"(?i)(password|passwd|secret|api_key|token|auth_key)\s*[=:]\s*['\"]?(?!REPLACE|CHANGE_ME|YOUR_|EXAMPLE|PLACEHOLDER|<)[A-Za-z0-9!@#$%^&*()_\-+=]{8,}['\"]?"
    ), r"\1=[REDACTED_SECRET]"),
    # MLC / iCloud app-specific passwords (16-char groups: xxxx-xxxx-xxxx-xxxx)
    (re.compile(r"\b[a-z]{4}-[a-z]{4}-[a-z]{4}-[a-z]{4}\b"), "[REDACTED_APP_PASSWORD]"),
]

# File extensions / names that should never be indexed (contain raw secrets).
SKIP_FILENAMES: frozenset[str] = frozenset({
    ".env",
    ".env.local",
    ".env.production",
    ".env.staging",
})

SKIP_EXTENSIONS: frozenset[str] = frozenset()  # populated below
_SKIP_EXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.env\.[^.]+$"),  # .env.* variants
]


def should_skip_file(filename: str) -> bool:
    """Return True if this file should be excluded from indexing entirely.

    .env.example and similar placeholder files are safe to index (they contain
    no real secrets) and are explicitly kept.
    """
    name = filename.lstrip("/").split("/")[-1]
    # Always allow placeholder/example env files — they're safe to index.
    if name.endswith(".example") or name.endswith(".example.env"):
        return False
    if name in SKIP_FILENAMES:
        return True
    for pat in _SKIP_EXT_PATTERNS:
        if pat.search(name):
            return True
    return False


def redact(text: str) -> str:
    """Apply all redaction rules to a text chunk. Returns the sanitized string."""
    for pattern, replacement in _REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text
