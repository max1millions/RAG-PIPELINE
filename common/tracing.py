"""LangSmith tracing for RAG pipeline (retrieval, indexing, eval)."""

from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

from common.config import load_config

F = TypeVar("F", bound=Callable[..., Any])

_tracing_initialized = False


def tracing_enabled() -> bool:
    if os.environ.get("ORION_RAG_TRACING", "").lower() in ("0", "false", "no"):
        return False
    cfg = load_config().get("langsmith", {}) or {}
    if cfg.get("enabled") is False:
        return False
    if not os.environ.get("LANGSMITH_API_KEY") and not os.environ.get("LANGCHAIN_API_KEY"):
        return False
    return bool(
        os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("true", "1", "yes")
        or os.environ.get("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
        or cfg.get("enabled") is True
    )


def init_tracing() -> None:
    global _tracing_initialized
    if _tracing_initialized:
        return
    _tracing_initialized = True
    cfg = load_config().get("langsmith", {}) or {}
    if not tracing_enabled() and cfg.get("enabled") is not True:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    project = cfg.get("project") or os.environ.get("LANGCHAIN_PROJECT") or "orion-rag"
    os.environ.setdefault("LANGCHAIN_PROJECT", str(project))
    if cfg.get("endpoint"):
        os.environ.setdefault("LANGCHAIN_ENDPOINT", str(cfg["endpoint"]))


def traceable_rag(
    *,
    name: str | None = None,
    run_type: str = "chain",
    **kwargs: Any,
) -> Callable[[F], F]:
    """LangSmith @traceable when enabled; no-op otherwise."""

    def decorator(fn: F) -> F:
        if not tracing_enabled():
            return fn
        init_tracing()
        try:
            from langsmith import traceable
        except ImportError:
            return fn
        return traceable(name=name or fn.__name__, run_type=run_type, **kwargs)(fn)  # type: ignore[return-value]

    return decorator
