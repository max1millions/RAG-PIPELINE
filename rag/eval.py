#!/usr/bin/env python3
"""RAG recall evaluation against eval_cases.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from common.config import load_config, require_feature
from common.tracing import init_tracing, traceable_rag, tracing_enabled
from rag.retrieve import retrieve, retrieve_to_context_block
from rag.settings import CHROMA_DIR, top_k_default


def _eval_path() -> Path:
    """Return overlay eval_cases.yaml if present, else the in-repo file."""
    from common.paths import overlay_root, stack_root

    ov = overlay_root()
    if ov:
        ovp = ov / "config" / "eval_cases.yaml"
        if ovp.exists():
            return ovp
    # Public in-repo file (may be eval_cases.example.yaml after Phase 3)
    live = stack_root() / "rag" / "eval_cases.yaml"
    if live.exists():
        return live
    return stack_root() / "rag" / "eval_cases.example.yaml"


EVAL_PATH = _eval_path()


def _match_hit(expect: str, hits: list, *, path_only: bool) -> bool:
    if not expect:
        return False
    needle = expect.lower()
    for h in hits:
        path = f"{h.repo or ''}/{h.path}".lower()
        if needle in path:
            return True
        if not path_only and needle in (h.text[:200] or "").lower():
            return True
    return False


@traceable_rag(name="orion_rag_eval", run_type="chain")
def run_eval(*, k: int | None = None, hybrid: bool = False) -> dict:
    require_feature("rag", "RAG")
    k = k or top_k_default()
    cases = yaml.safe_load(EVAL_PATH.read_text(encoding="utf-8")) or []
    passed = 0
    rows: list[dict] = []
    total_context_chars = 0

    for case in cases:
        query = case["query"]
        expect = case.get("expect_path_contains") or case.get("expect_contains") or ""
        repo = case.get("repo")
        intent = case.get("intent", "auto")
        cols = tuple(case["collections"]) if case.get("collections") else None
        path_only = bool(case.get("match_path_only", False))

        hits = retrieve(
            query,
            repo=repo,
            k=k,
            collections=cols,
            hybrid=hybrid,
            intent=intent,
        )
        ok = _match_hit(expect, hits, path_only=path_only)
        if ok:
            passed += 1
        context = retrieve_to_context_block(hits)
        total_context_chars += len(context)
        rows.append(
            {
                "id": case.get("id", query[:40]),
                "ok": ok,
                "expect": expect,
                "top_path": hits[0].path if hits else "",
                "top_distance": round(hits[0].distance, 4) if hits else None,
                "hit_count": len(hits),
                "context_chars": len(context),
                "context_chunks": context.count("\n\n") + (1 if context else 0),
            }
        )

    rate = passed / len(cases) if cases else 0.0
    avg_ctx = total_context_chars / len(cases) if cases else 0.0
    return {
        "passed": passed,
        "total": len(cases),
        "rate": rate,
        "avg_context_chars": avg_ctx,
        "rows": rows,
        "eval_path": str(EVAL_PATH),
        "k": k,
        "hybrid": hybrid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG recall@k")
    parser.add_argument("--k", type=int, help="Top-k per case")
    parser.add_argument("--hybrid", action="store_true", help="Use hybrid retrieval")
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="Write full result JSON to PATH (also prints human summary)",
    )
    args = parser.parse_args()

    load_config()
    init_tracing()
    require_feature("rag", "RAG")

    if not CHROMA_DIR.exists():
        print(f"ERROR: index missing at {CHROMA_DIR}", file=sys.stderr)
        return 1

    cfg = load_config()
    threshold = float(cfg.get("limits", {}).get("eval_recall_threshold", 0.7))
    result = run_eval(k=args.k, hybrid=args.hybrid)

    for row in result["rows"]:
        mark = "PASS" if row["ok"] else "FAIL"
        dist = row.get("top_distance")
        dist_s = f" d={dist}" if dist is not None else ""
        print(
            f"{mark}  {row['id']}: expect {row['expect']!r} -> {row['top_path']}"
            f"{dist_s} ctx={row.get('context_chars', 0)}"
        )

    print(
        f"\nRecall@{args.k or top_k_default()}: "
        f"{result['passed']}/{result['total']} = {result['rate']:.1%} "
        f"(threshold {threshold:.0%}); "
        f"avg context chars={result['avg_context_chars']:.0f}"
    )
    if tracing_enabled():
        print("LangSmith tracing enabled — see project orion-rag in LangSmith UI.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Wrote {out}")

    return 0 if result["rate"] >= threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
