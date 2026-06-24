#!/usr/bin/env python3
"""RAG recall evaluation against eval_cases.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from common.config import load_config, require_feature
from common.tracing import init_tracing, traceable_rag, tracing_enabled
from rag.retrieve import retrieve
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


@traceable_rag(name="orion_rag_eval", run_type="chain")
def run_eval(*, k: int | None = None, hybrid: bool = False) -> dict:
    require_feature("rag", "RAG")
    k = k or top_k_default()
    cases = yaml.safe_load(EVAL_PATH.read_text(encoding="utf-8")) or []
    passed = 0
    rows: list[dict] = []

    for case in cases:
        query = case["query"]
        expect = case.get("expect_path_contains") or case.get("expect_contains") or ""
        repo = case.get("repo")
        intent = case.get("intent", "auto")
        cols = tuple(case["collections"]) if case.get("collections") else None

        hits = retrieve(
            query,
            repo=repo,
            k=k,
            collections=cols,
            hybrid=hybrid,
            intent=intent,
        )
        hit_paths = " ".join(
            f"{h.repo or ''}/{h.path} {h.text[:200]}" for h in hits
        ).lower()
        ok = expect.lower() in hit_paths
        if ok:
            passed += 1
        rows.append(
            {
                "id": case.get("id", query[:40]),
                "ok": ok,
                "expect": expect,
                "top_path": hits[0].path if hits else "",
            }
        )

    rate = passed / len(cases) if cases else 0.0
    return {"passed": passed, "total": len(cases), "rate": rate, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG recall@k")
    parser.add_argument("--k", type=int, help="Top-k per case")
    parser.add_argument("--hybrid", action="store_true", help="Use hybrid retrieval")
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
        print(f"{mark}  {row['id']}: expect {row['expect']!r} -> {row['top_path']}")

    print(
        f"\nRecall@{args.k or top_k_default()}: "
        f"{result['passed']}/{result['total']} = {result['rate']:.1%} "
        f"(threshold {threshold:.0%})"
    )
    if tracing_enabled():
        print("LangSmith tracing enabled — see project orion-rag in LangSmith UI.")

    return 0 if result["rate"] >= threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
