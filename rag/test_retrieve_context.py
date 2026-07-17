"""Unit tests for context relevance filtering (no Chroma required)."""

from __future__ import annotations

from rag.retrieve import Hit, filter_hits_for_context, retrieve_to_context_block


def _hit(path: str, distance: float, *, chunk: int = 0, repo: str = "REPO", text: str = "") -> Hit:
    body = text or ("x" * 500)
    return Hit(
        repo=repo,
        path=path,
        chunk=chunk,
        distance=distance,
        text=body,
        collection="repos",
        kind="code",
    )


def test_filter_drops_weak_and_dedupes_paths() -> None:
    hits = [
        _hit("a.py", 0.20, chunk=0),
        _hit("a.py", 0.22, chunk=1),
        _hit("b.py", 0.35, chunk=0),
        _hit("c.py", 0.50, chunk=0),
        _hit("d.py", 0.95, chunk=0),  # above max_distance
    ]
    selected = filter_hits_for_context(
        hits,
        max_chunks=6,
        max_distance=0.85,
        strong_distance=0.40,
        early_stop_strong=3,
        max_chunks_per_path=1,
    )
    paths = [h.path for h in selected]
    assert "d.py" not in paths
    assert paths.count("a.py") == 1
    assert len(selected) <= 3  # early-stop once 3 strong (0.20, 0.35 — only 2 strong; plus 0.50)
    # a (0.20 strong), b (0.35 strong), c (0.50 not strong) → 2 strong, continues to c then stops at max or early
    assert "a.py" in paths and "b.py" in paths


def test_early_stop_on_strong_hits() -> None:
    hits = [
        _hit("a.py", 0.10),
        _hit("b.py", 0.15),
        _hit("c.py", 0.18),
        _hit("d.py", 0.55),
        _hit("e.py", 0.60),
    ]
    selected = filter_hits_for_context(
        hits,
        max_chunks=6,
        max_distance=0.85,
        strong_distance=0.40,
        early_stop_strong=3,
        max_chunks_per_path=1,
    )
    assert [h.path for h in selected] == ["a.py", "b.py", "c.py"]


def test_context_block_adaptive_budget() -> None:
    hits = [
        _hit("strong.py", 0.15, text="S" * 2000),
        _hit("weak.py", 0.80, text="W" * 2000),
    ]
    block = retrieve_to_context_block(
        hits,
        max_chars=900,
        max_chunks=6,
        filter_context=True,
    )
    assert "strong.py" in block
    # Weak snippet should be shorter than full budget.
    weak_section = block.split("weak.py")[-1] if "weak.py" in block else ""
    assert len(weak_section) < 900 + 50


def test_filter_keeps_best_when_all_weak() -> None:
    hits = [_hit("only.py", 0.99, text="lonely")]
    selected = filter_hits_for_context(
        hits,
        max_chunks=6,
        max_distance=0.85,
        strong_distance=0.40,
        early_stop_strong=3,
        max_chunks_per_path=1,
    )
    assert len(selected) == 1
    assert selected[0].path == "only.py"
