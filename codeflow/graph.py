"""LangGraph workflow definition."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from codeflow.nodes import (
    AgentState,
    apply_changes_node,
    coder_node,
    fetch_rag_node,
    finalize_error_node,
    git_commit_node,
    planner_node,
    review_node,
    syntax_check_node,
    test_run_node,
    triage_node,
)


def _route_after_triage(state: AgentState) -> str:
    if state.get("plan_path") and state.get("plan"):
        return "coder"
    if state.get("complexity") == "simple":
        return "coder"
    return "planner"


def _route_after_review(state: AgentState) -> str:
    if state.get("approved"):
        return "commit"
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 3)
    if iteration >= max_iter:
        return "fail"
    if state.get("replan"):
        return "planner"
    return "coder"


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("triage", triage_node)
    g.add_node("fetch_rag", fetch_rag_node)
    g.add_node("planner", planner_node)
    g.add_node("coder", coder_node)
    g.add_node("apply", apply_changes_node)
    g.add_node("syntax", syntax_check_node)
    g.add_node("test_run", test_run_node)
    g.add_node("review", review_node)
    g.add_node("commit", git_commit_node)
    g.add_node("fail", finalize_error_node)

    g.add_edge(START, "triage")
    g.add_edge("triage", "fetch_rag")
    g.add_conditional_edges("fetch_rag", _route_after_triage, {"planner": "planner", "coder": "coder"})
    g.add_edge("planner", "coder")
    g.add_edge("coder", "apply")
    g.add_edge("apply", "syntax")
    g.add_edge("syntax", "test_run")
    g.add_edge("test_run", "review")
    g.add_conditional_edges(
        "review",
        _route_after_review,
        {"commit": "commit", "coder": "coder", "planner": "planner", "fail": "fail"},
    )
    g.add_edge("commit", END)
    g.add_edge("fail", END)

    return g.compile()
