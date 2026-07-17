"""LangGraph node implementations."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from codeflow.file_context import gather_file_context
from codeflow.plan_io import read_plan, write_plan
from codeflow.patch_apply import apply_edits
from codeflow.test_runner import format_test_results, run_tests
from codeflow.safety_gate import check_diff
from common.config import anthropic_api_key, feature_enabled, load_config
from common.paths import git_bin as _git_bin, openclaw_bin_dir as _openclaw_bin_dir
from rag.retrieve import retrieve, retrieve_to_context_block

STACK_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class AgentState(TypedDict, total=False):
    request: str
    repo: str
    repo_path: str
    complexity: str
    plan: str
    plan_path: str
    rag_context: str
    db_context: str
    coder_output: dict[str, Any]
    changed_files: list[str]
    syntax_results: str
    test_results: str
    test_feedback: str
    test_passed: bool
    review_feedback: str
    approved: bool
    iteration: int
    max_iterations: int
    commit_sha: str
    pushed: bool
    pr_url: str
    incident_fingerprint: str
    test_cmd_override: str
    test_file: str
    force_push: bool
    error: str
    summary: str
    replan: bool
    needs_clarification: bool
    clarifying_questions: list[str]
    status: str


def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError as e:
            return {"error": f"JSON syntax error: {e}\nRaw LLM output:\n{text[:1000]}"}
            
    return {"error": f"Could not parse JSON from LLM output. Raw LLM output:\n{text[:1000]}"}


def _llm(model: str, system: str, user: str) -> str:
    anthropic_api_key()
    chat = ChatAnthropic(model=model, max_tokens=8192, temperature=0)
    resp = chat.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    content = resp.content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "".join(parts)
    return str(content)


def triage_node(state: AgentState) -> AgentState:
    cfg = load_config()
    model = cfg.get("models", {}).get("triage", "claude-sonnet-4-6")
    system = _read_prompt("triage.md")
    user = f"Repo: {state['repo']}\nRequest: {state['request']}"
    raw = _llm(model, system, user)
    parsed = _extract_json(raw)
    return {
        **state,
        "complexity": parsed.get("complexity", "complex"),
        "iteration": state.get("iteration", 0),
    }


def fetch_rag_node(state: AgentState) -> AgentState:
    if state.get("rag_context"):
        return state
    if not feature_enabled("rag"):
        return {**state, "rag_context": ""}
    try:
        intent = "incident" if state.get("incident_fingerprint") else "general"
        hits = retrieve(
            state["request"],
            repo=state["repo"],
            k=6,
            hybrid=False,
            intent=intent,
        )
        # Cap snippet size; relevance filtering / path dedupe applied in assembler.
        context = retrieve_to_context_block(hits, max_chars=1200)
    except Exception as exc:
        context = f"(RAG unavailable: {exc})"
    return {**state, "rag_context": context}


def _parse_clarification(raw: str) -> dict[str, Any] | None:
    """Return clarification payload if planner asked questions; else None."""
    parsed = _extract_json(raw)
    if parsed.get("error") or parsed.get("status") != "needs_clarification":
        return None
    questions = parsed.get("questions") or []
    if isinstance(questions, str):
        questions = [questions]
    cleaned = [str(q).strip() for q in questions if str(q).strip()]
    if not cleaned:
        return None
    return {
        "questions": cleaned[:3],
        "reason": str(parsed.get("reason") or "Requirements are ambiguous.").strip(),
    }


def _planner_user_message(state: AgentState, *, force_plan: bool = False) -> str:
    db_block = state.get("db_context") or ""
    incident = bool(state.get("incident_fingerprint"))
    parts = [
        f"Repo: {state['repo']}",
        f"Request: {state['request']}",
        "",
        f"RAG context:\n{state.get('rag_context', '')}",
        "",
        f"Local DB schema:\n{db_block}",
        "",
        f"Prior feedback (if any):\n{state.get('review_feedback', '')}",
        f"{state.get('test_feedback', '')}",
    ]
    if incident or force_plan:
        parts.append(
            "\nClarification is NOT allowed for this run (incident/auto-fix or forced). "
            "You MUST produce an implementation plan (Option A). "
            "Make reasonable assumptions and document them under Risks."
        )
    return "\n".join(parts)


def planner_node(state: AgentState) -> AgentState:
    cfg = load_config()
    model = cfg.get("models", {}).get("planner", "claude-opus-4-6")
    system = _read_prompt("planner.md")
    clarification_enabled = feature_enabled("planner_clarification")
    incident = bool(state.get("incident_fingerprint"))

    raw = _llm(model, system, _planner_user_message(state))
    clarification = _parse_clarification(raw) if clarification_enabled else None

    # Incidents must never pause for the user — re-prompt once, then synthesize if needed.
    if clarification and incident:
        raw = _llm(model, system, _planner_user_message(state, force_plan=True))
        clarification = _parse_clarification(raw)
        if clarification:
            qs = clarification["questions"]
            reason = clarification["reason"]
            raw = (
                "# Goal\n"
                "Best-effort incident fix (clarification not allowed).\n\n"
                f"## Open questions treated as assumptions\n"
                f"Reason blocked: {reason}\n"
                + "\n".join(
                    f"- {q} — choose the most production-safe, minimal change"
                    for q in qs
                )
                + "\n\n## Files to modify\n"
                "Infer from the request and RAG context; touch only what is required.\n\n"
                "## Step-by-step changes\n"
                "1. Locate the failing path from the incident request/stack.\n"
                "2. Apply the smallest fix consistent with existing patterns.\n"
                "3. Keep logging/error handling style unchanged unless required.\n\n"
                "## Syntax/validation checks\n"
                "Run repo syntax/tests as configured for this fix run.\n\n"
                "## Risks and rollback notes\n"
                "Assumptions above may be wrong; PR review is the HITL gate. "
                "Revert the orion-branch commit if behavior is incorrect.\n"
            )
            clarification = None

    # Feature off: treat any clarification JSON as needing a forced plan.
    if not clarification_enabled:
        maybe = _parse_clarification(raw)
        if maybe:
            raw = _llm(model, system, _planner_user_message(state, force_plan=True))
            still = _parse_clarification(raw)
            if still:
                qs = still["questions"]
                raw = (
                    "# Goal\nProceed with best-effort plan (clarification disabled).\n\n"
                    "## Assumptions\n"
                    + "\n".join(f"- {q}" for q in qs)
                    + "\n\n## Steps\nImplement the minimal fix from the request and RAG context.\n"
                )

    if clarification and clarification_enabled and not incident:
        reason = clarification["reason"]
        questions = clarification["questions"]
        return {
            **state,
            "needs_clarification": True,
            "clarifying_questions": questions,
            "status": "needs_clarification",
            "summary": reason,
            "error": "",
            "plan": "",
            "plan_path": "",
            "replan": False,
            "approved": False,
        }

    plan = raw
    plan_path = write_plan(state["repo"], state["request"], plan)
    return {
        **state,
        "plan": plan,
        "plan_path": str(plan_path),
        "replan": False,
        "needs_clarification": False,
        "clarifying_questions": [],
        "status": "",
    }


def coder_node(state: AgentState) -> AgentState:
    cfg = load_config()
    limits = cfg.get("limits", {})
    model = cfg.get("models", {}).get("coder", "claude-sonnet-4-6")
    system = _read_prompt("coder.md")
    plan_path = state.get("plan_path") or ""
    if plan_path:
        try:
            plan_block = read_plan(plan_path)
        except OSError:
            plan_block = state.get("plan") or "(plan file unreadable — use request only)"
    else:
        plan_block = state.get("plan") or "(direct implementation — no separate plan)"
    db_block = state.get("db_context") or ""
    repo_path = Path(state["repo_path"])
    file_context = gather_file_context(
        repo_path=repo_path,
        request=state["request"],
        plan=plan_block,
        rag_context=state.get("rag_context", ""),
        repo=state["repo"],
        changed_files=state.get("changed_files"),
        max_file_chars=int(limits.get("coder_max_file_context_chars", 8000)),
        max_total_chars=int(limits.get("coder_max_total_context_chars", 40000)),
    )
    user = (
        f"Repo: {state['repo']}\n"
        f"Repo path: {state['repo_path']}\n"
        f"Request: {state['request']}\n\n"
        f"Plan file: {plan_path or '(inline)'}\n"
        f"Plan:\n{plan_block}\n\n"
        f"RAG context:\n{state.get('rag_context', '')}\n\n"
        f"Local DB schema:\n{db_block}\n\n"
        f"Target files:\n{file_context}\n\n"
        f"Review feedback:\n{state.get('review_feedback', '')}\n\n"
        f"Apply errors (fix these):\n{state.get('error', '')}\n\n"
        f"Test failures (fix these):\n{state.get('test_feedback', '')}"
    )
    raw = _llm(model, system, user)
    parsed = _extract_json(raw)
    if parsed.get("error"):
        return {**state, "error": parsed["error"], "approved": False, "coder_output": {}}
    return {**state, "coder_output": parsed, "error": ""}


def apply_changes_node(state: AgentState) -> AgentState:
    if state.get("error"):
        return {**state, "changed_files": []}

    output = state.get("coder_output") or {}
    edits = output.get("edits") or []
    if not isinstance(edits, list):
        return {
            **state,
            "error": "LLM returned invalid format for 'edits' (expected a list).",
            "approved": False,
            "changed_files": []
        }
    
    repo_path = Path(state["repo_path"])

    result = apply_edits(edits, repo_path)
    if not result.ok:
        err = "; ".join(result.errors)
        return {
            **state,
            "error": err,
            "approved": False,
            "review_feedback": err,
            "changed_files": [],
        }

    return {**state, "changed_files": result.changed_files, "error": ""}


def syntax_check_node(state: AgentState) -> AgentState:
    repo_path = Path(state["repo_path"])
    cfg = load_config()
    timeout = int(cfg.get("limits", {}).get("subprocess_timeout_s", 120))
    results: list[str] = []

    for rel in state.get("changed_files") or []:
        fpath = repo_path / rel
        suffix = fpath.suffix.lower()
        cmd: list[str] | None = None
        if suffix == ".py":
            cmd = ["python3", "-m", "py_compile", str(fpath)]
        elif suffix == ".sh":
            cmd = ["bash", "-n", str(fpath)]
        elif suffix == ".php":
            cmd = ["php", "-l", str(fpath)]

        if cmd is None:
            results.append(f"{rel}: (no syntax checker)")
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if proc.returncode == 0:
                results.append(f"{rel}: OK")
            else:
                results.append(f"{rel}: FAIL\n{proc.stderr or proc.stdout}")
        except FileNotFoundError:
            results.append(f"{rel}: SKIP (checker binary missing)")
        except subprocess.TimeoutExpired:
            results.append(f"{rel}: TIMEOUT")

    syntax = "\n".join(results)
    failed = any("FAIL" in r or "TIMEOUT" in r for r in results)
    
    # Do not overwrite a prior rejection (e.g. from coder error) if there were no checks
    new_approved = state.get("approved", True)
    if results:
        new_approved = not failed
        
    return {**state, "syntax_results": syntax, "approved": new_approved}


def test_run_node(state: AgentState) -> AgentState:
    if state.get("error"):
        return {
            **state,
            "test_results": "skipped (coder/apply failed)",
            "test_passed": False,
        }

    if state.get("syntax_results") and "FAIL" in state["syntax_results"]:
        return {
            **state,
            "test_results": "skipped (syntax failed)",
            "test_passed": False,
            "test_feedback": state.get("syntax_results", ""),
        }

    repo_path = Path(state["repo_path"])
    result = run_tests(
        state["repo"],
        repo_path,
        changed_files=state.get("changed_files"),
        test_cmd_override=state.get("test_cmd_override"),
        test_file=state.get("test_file"),
    )
    formatted = format_test_results(result)
    passed = bool(result.get("passed"))

    feedback = ""
    if not passed:
        feedback = formatted

    return {
        **state,
        "test_results": formatted,
        "test_passed": passed,
        "test_feedback": feedback if not passed else state.get("test_feedback", ""),
        "approved": passed,
    }


def _git_diff_snippet(repo_path: Path, *, max_chars: int = 2000) -> str:
    git = _git_bin()
    env = os.environ.copy()
    claw_bin = _openclaw_bin_dir()
    if claw_bin:
        env["PATH"] = str(claw_bin) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [git, "diff", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        diff = (proc.stdout or "").strip()
        if not diff:
            proc = subprocess.run(
                [git, "diff"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            diff = (proc.stdout or "").strip()
    except (subprocess.TimeoutExpired, OSError):
        return "(diff unavailable)"
    if not diff:
        return "(no diff)"
    if len(diff) > max_chars:
        return diff[:max_chars] + "\n... [diff truncated]"
    return diff


def review_node(state: AgentState) -> AgentState:
    cfg = load_config()
    model = cfg.get("models", {}).get("triage", "claude-sonnet-4-6")
    system = _read_prompt("review.md")

    syntax_failed = state.get("syntax_results") and "FAIL" in state["syntax_results"]
    tests_failed = state.get("test_passed") is False

    if syntax_failed or tests_failed or state.get("error"):
        iteration = state.get("iteration", 0) + 1
        feedback_parts = []
        if state.get("error"):
            feedback_parts.append(f"Coder/Apply Error:\n{state['error']}")
        if syntax_failed:
            feedback_parts.append(state.get("syntax_results", ""))
        if tests_failed:
            feedback_parts.append(state.get("test_results", ""))
        combined = "\n\n".join(feedback_parts)
        return {
            **state,
            "approved": False,
            "review_feedback": combined[:4000],
            "test_feedback": combined[:4000],
            "iteration": iteration,
            "replan": iteration >= 2,
        }

    diff_block = _git_diff_snippet(Path(state["repo_path"]))
    user = (
        f"Request: {state['request']}\n"
        f"Changed files: {state.get('changed_files')}\n"
        f"Diff:\n{diff_block}\n"
        f"Syntax:\n{state.get('syntax_results')}\n"
        f"Tests:\n{state.get('test_results')}\n"
        f"Error: {state.get('error')}"
    )
    raw = _llm(model, system, user)
    parsed = _extract_json(raw)
    approved = bool(parsed.get("approved")) and not state.get("error")
    iteration = state.get("iteration", 0) + 1
    return {
        **state,
        "approved": approved,
        "review_feedback": parsed.get("feedback", ""),
        "iteration": iteration,
        "replan": iteration >= 2 and not approved,
    }


def _create_pr(repo_path: Path, repo: str, state: AgentState, timeout: int) -> str:
    if not feature_enabled("auto_pr"):
        return ""

    fp = state.get("incident_fingerprint") or ""
    fp_short = fp[:8] if fp else ""
    title = (state.get("coder_output") or {}).get("commit_message") or state["request"][:72]
    body_parts = [
        "## Orion auto-fix",
        "",
        f"**Repo:** {repo}",
        f"**Request:** {state['request'][:500]}",
    ]
    if fp_short:
        body_parts.append(f"**Incident:** `{fp_short}`")
    if state.get("test_results"):
        body_parts.append("")
        body_parts.append("### Test output")
        body_parts.append("```")
        body_parts.append(str(state["test_results"])[:2000])
        body_parts.append("```")

    body = "\n".join(body_parts)

    view = subprocess.run(
        ["gh", "pr", "view", "orion", "--json", "url"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if view.returncode == 0 and view.stdout.strip():
        try:
            data = json.loads(view.stdout)
            url = data.get("url")
            if url:
                return str(url)
        except json.JSONDecodeError:
            pass

    create = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            "orion",
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if create.returncode == 0:
        return (create.stdout or create.stderr or "").strip()
    err = (create.stderr or create.stdout or "").strip()
    if "already exists" in err.lower():
        view2 = subprocess.run(
            ["gh", "pr", "view", "orion", "--json", "url"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if view2.returncode == 0:
            try:
                return str(json.loads(view2.stdout).get("url") or "")
            except json.JSONDecodeError:
                pass
    return f"(PR not created: {err[:200]})"


def git_commit_node(state: AgentState) -> AgentState:
    repo_path = Path(state["repo_path"])
    cfg = load_config()
    timeout = int(cfg.get("limits", {}).get("subprocess_timeout_s", 120))
    git = _git_bin()

    env = os.environ.copy()
    claw_bin = _openclaw_bin_dir()
    if claw_bin:
        env["PATH"] = str(claw_bin) + os.pathsep + env.get("PATH", "")

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    push_branch_proc = run(["config", "--get", "hooks.allowed-push-branch"])
    push_branch = (
        push_branch_proc.stdout.strip()
        if push_branch_proc.returncode == 0 and push_branch_proc.stdout.strip()
        else "orion"
    )
    main_only = push_branch == "main"

    checkout = run(["checkout", push_branch])
    if checkout.returncode != 0:
        run(["checkout", "-b", push_branch])

    run(["add", "-A"])
    msg = (state.get("coder_output") or {}).get("commit_message") or state["request"][:72]
    commit = run(["commit", "-m", msg])
    if commit.returncode != 0 and "nothing to commit" in (commit.stdout + commit.stderr):
        return {
            **state,
            "summary": "No changes to commit.",
            "commit_sha": "",
            "pushed": False,
            "pr_url": "",
        }

    sha_proc = run(["rev-parse", "HEAD"])
    sha = sha_proc.stdout.strip() if sha_proc.returncode == 0 else ""

    should_push = state.get("force_push") or feature_enabled("auto_push_orion")
    pushed = False
    pr_url = ""
    if should_push:
        gate = check_diff(repo_path, env=env)
        if not gate.get("passed"):
            reasons = "; ".join(gate.get("reasons") or [])
            return {
                **state,
                "commit_sha": sha,
                "pushed": False,
                "pr_url": "",
                "approved": False,
                "error": f"Safety gate blocked push: {reasons}",
                "summary": f"Commit on {push_branch} ({sha[:8]}) but push blocked: {reasons}",
            }
        push = run(["push", "-u", "origin", push_branch])
        pushed = push.returncode == 0
        if pushed and not main_only:
            try:
                pr_url = _create_pr(repo_path, state["repo"], state, timeout)
            except FileNotFoundError:
                pr_url = "(gh not installed)"
            except subprocess.TimeoutExpired:
                pr_url = "(gh pr create timed out)"

    files = ", ".join(state.get("changed_files") or [])
    summary = f"Committed on {push_branch} ({sha[:8]}): {files}"
    if pushed:
        summary += f" — pushed to origin/{push_branch}"
        if not main_only:
            if pr_url and pr_url.startswith("http"):
                summary += f" — PR: {pr_url}"
            elif pr_url:
                summary += f" — {pr_url}"
    else:
        summary += " — push deferred (set auto_push_orion or use --push)"

    return {
        **state,
        "commit_sha": sha,
        "pushed": pushed,
        "pr_url": pr_url,
        "summary": summary,
    }


def finalize_error_node(state: AgentState) -> AgentState:
    err = (
        state.get("error")
        or state.get("test_feedback")
        or state.get("review_feedback")
        or "Review failed"
    )
    return {
        **state,
        "summary": f"Code change failed after {state.get('iteration', 0)} iteration(s): {err[:500]}",
    }
