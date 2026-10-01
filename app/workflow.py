"""A real LangGraph pause/resume, with no external actions or model calls."""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class ReviewState(TypedDict):
    assessment: dict
    approval: dict | None
    status: str


def prepare_sample(state: ReviewState):
    # The assessment was durably versioned before this workflow began.
    return {"status": "AWAITING_REVIEW"}


def human_review(state: ReviewState):
    assessment = state["assessment"]
    decision = interrupt({"assessment": assessment, "allowed": ["APPROVE", "SKIP"]})
    # Defense in depth: never resume a checkpoint using another version's approval.
    if (decision["job_id"] != assessment["job_id"] or
            decision["assessment_version"] != assessment["version"] or
            decision["decision"] not in ("APPROVE", "SKIP")):
        raise ValueError("Approval does not match the interrupted assessment")
    return {"approval": decision,
            "status": "REVIEW_APPROVED" if decision["decision"] == "APPROVE" else "SKIPPED"}


def build_graph(checkpointer):
    graph = StateGraph(ReviewState)
    graph.add_node("prepare_sample", prepare_sample)
    graph.add_node("human_review", human_review)
    graph.add_edge(START, "prepare_sample")
    graph.add_edge("prepare_sample", "human_review")
    graph.add_edge("human_review", END)
    return graph.compile(checkpointer=checkpointer)
