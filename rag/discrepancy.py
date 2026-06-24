"""Scan REPOS for doc/code/config discrepancies."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rag.chunking import chunk_file
from rag.settings import repos_root, collection_names
from rag.chroma_store import chunk_id, chroma_metadata

_PATH_REF = re.compile(
    r"`([^`]+\.(?:py|sh|sql|php|js|ts|yml|yaml|json|md))`"
    r"|(?:^|\s)([\w./-]+\.(?:py|sh|sql|php|js|ts|yml|yaml))\b",
    re.MULTILINE,
)
_ENV_KEY = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


def extract_path_refs(text: str) -> list[str]:
    refs: list[str] = []
    for m in _PATH_REF.finditer(text):
        ref = (m.group(1) or m.group(2) or "").strip()
        if ref and not ref.startswith("http"):
            refs.append(ref.lstrip("./"))
    return list(dict.fromkeys(refs))


def scan_repo(repo_path: Path, readme_text: str | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    repo_name = repo_path.name
    if not readme_text:
        return findings

    for ref in extract_path_refs(readme_text):
        candidate = repo_path / ref
        if not candidate.exists() and "/" not in ref:
            alt = repo_path / Path(ref).name
            if not alt.exists():
                findings.append(
                    {
                        "repo": repo_name,
                        "severity": "warning",
                        "kind": "discrepancy",
                        "summary": (
                            f"{repo_name} README references `{ref}` but no matching file "
                            f"exists under the repo root."
                        ),
                        "related_paths": ref,
                        "check": "readme_missing_path",
                    }
                )

    env_example = repo_path / ".env.example"
    if env_example.is_file():
        try:
            example_keys = set(_ENV_KEY.findall(env_example.read_text(encoding="utf-8", errors="replace")))
            readme_keys = set(_ENV_KEY.findall(readme_text))
            readme_only = readme_keys - example_keys - {
                "README",
                "SQL",
                "API",
                "CWR",
                "MCP",
                "URL",
                "HTTP",
                "HTTPS",
            }
            for key in sorted(k for k in readme_only if len(k) > 4 and "_" in k)[:5]:
                findings.append(
                    {
                        "repo": repo_name,
                        "severity": "info",
                        "kind": "discrepancy",
                        "summary": (
                            f"{repo_name} README mentions env var `{key}` but it is not in "
                            f".env.example (may use a different config path)."
                        ),
                        "related_paths": ".env.example",
                        "check": "readme_env_not_in_example",
                    }
                )
        except OSError:
            pass

    return findings


def scan_path_map_drift(path_map_modules: dict[str, Any], repos: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for module, spec in (path_map_modules or {}).items():
        repos_name = (spec or {}).get("repos_name") or module
        if not (repos / repos_name).is_dir():
            findings.append(
                {
                    "repo": repos_name,
                    "severity": "warning",
                    "kind": "discrepancy",
                    "summary": (
                        f"path_map.yaml lists module `{module}` → `{repos_name}` but "
                        f"REPOS/{repos_name} is missing locally."
                    ),
                    "related_paths": "config/path_map.yaml",
                    "check": "path_map_missing_repo",
                }
            )
    return findings


def scan_all_repos() -> list[dict[str, Any]]:
    repos = repos_root()
    all_findings: list[dict[str, Any]] = []

    try:
        import yaml

        path_map_path = Path(__file__).resolve().parent.parent / "config" / "path_map.yaml"
        if path_map_path.is_file():
            pm = yaml.safe_load(path_map_path.read_text(encoding="utf-8")) or {}
            all_findings.extend(scan_path_map_drift(pm.get("modules") or {}, repos))
    except Exception:
        pass

    for repo_dir in sorted(p for p in repos.iterdir() if p.is_dir()):
        readme = repo_dir / "README.md"
        text = None
        if readme.is_file():
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = None
        all_findings.extend(scan_repo(repo_dir, text))

    return all_findings


def findings_to_index_docs(findings: list[dict[str, Any]]) -> tuple[list[str], list[str], list[dict]]:
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    coll = "discrepancies"
    for i, f in enumerate(findings):
        rel = f"finding_{i}.txt"
        doc = f["summary"]
        meta = chroma_metadata(
            {
                "repo": f.get("repo", "unknown"),
                "path": rel,
                "chunk": 0,
                "kind": "discrepancy",
                "severity": f.get("severity", "info"),
                "check": f.get("check", ""),
                "related_paths": f.get("related_paths", ""),
            }
        )
        ids.append(chunk_id(coll, rel, 0))
        documents.append(doc)
        metadatas.append(meta)
    return ids, documents, metadatas
