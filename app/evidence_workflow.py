"""Durable LangGraph orchestration for real evidence analysis and human review.

One thread per job/input version. Report rows and save journals remain in their
existing stores; checkpoints contain references, never an alternative score.
"""
from typing import TypedDict, Literal
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from .job_requirements import Strict
from .models import Job
from .retrieval import RetrievalError, now


class ReviewDecision(Strict):
    input_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["accept"]


class FlowState(TypedDict, total=False):
    job: dict
    input_version: str
    report_key: str
    status: str
    decision: dict


class EvidenceWorkflow:
    def __init__(self, manager):
        self.manager = manager
        self.path = str(manager.engine.path.parent / "evidence-workflow.sqlite3")

    def config(self, job, version):
        return {"configurable": {"thread_id": f"evidence-v1:{job.id}:{version}"}}

    def audit(self, state, status):
        self.manager.engine.put("evidence-workflow-event:" + str(uuid4()), {
            "job_id": state["job"]["id"], "input_version": state["input_version"],
            "status": status, "at": now()})
        return {"status": status}

    def graph(self, saver):
        from .role_analysis import AnalysisRun
        manager = self.manager

        def step(state, extract_only):
            job = Job.model_validate(state["job"])
            run = AnalysisRun.model_validate(manager.engine.get(state["report_key"]))
            try:
                manager.current(job, state["input_version"])
                if run.state != "COMPLETE":
                    run.state, run.error = "RUNNING", ""
                    if extract_only:
                        manager.extract_run(job, state["report_key"], run)
                    else:
                        manager.assess_run(job, state["report_key"], run)
            except RetrievalError as exc:
                run.state = "PAUSED" if exc.status in (409, 503) else "ERROR"
                run.stage, run.error = "Analysis stopped; completed work is saved.", str(exc)
            except Exception:
                run.state, run.stage, run.error = "ERROR", "Analysis stopped; completed work is saved.", "Workflow processing failed. Completed work is retained."
            manager.save(state["report_key"], run)
            status = "AWAITING_REVIEW" if run.state == "COMPLETE" else "ANALYZING" if run.state == "RUNNING" else "PAUSED"
            return self.audit(state, status)

        def retry(state):
            decision = interrupt({"kind": "analysis_paused", "job_id": state["job"]["id"],
                                  "input_version": state["input_version"], "allowed": ["retry"]})
            if decision != {"action": "retry", "input_version": state["input_version"]}:
                raise RetrievalError("Retry does not match this analysis version.", 409)
            return self.audit(state, "ANALYZING")

        def review(state):
            decision = interrupt({"kind": "evidence_review", "job_id": state["job"]["id"],
                                  "input_version": state["input_version"], "allowed": ["accept", "revise"]})
            if not isinstance(decision, dict) or decision.get("input_version") != state["input_version"] or decision.get("action") not in ("accept", "revise"):
                raise RetrievalError("Review does not match this analysis version.", 409)
            return {"decision": decision}

        def finish(state):
            return self.audit(state, "REVIEWED" if state["decision"]["action"] == "accept" else "REVISION_REQUESTED")

        graph = StateGraph(FlowState)
        graph.add_node("extract_requirements", lambda s: step(s, True))
        graph.add_node("assess_evidence", lambda s: step(s, False))
        graph.add_node("await_retry", retry)
        graph.add_node("await_review", review)
        graph.add_node("record_review", finish)
        graph.add_edge(START, "extract_requirements")
        graph.add_conditional_edges("extract_requirements", lambda s: "await_retry" if s["status"] == "PAUSED" else "await_review" if s["status"] == "AWAITING_REVIEW" else "assess_evidence")
        graph.add_conditional_edges("assess_evidence", lambda s: "await_review" if s["status"] == "AWAITING_REVIEW" else "await_retry")
        graph.add_edge("await_retry", "extract_requirements")
        graph.add_edge("await_review", "record_review")
        graph.add_edge("record_review", END)
        return graph.compile(checkpointer=saver)

    def run(self, job, key, run):
        config = self.config(job, run.input_version)
        with SqliteSaver.from_conn_string(self.path) as saver:
            graph = self.graph(saver)
            snapshot = graph.get_state(config)
            if not snapshot.values:
                graph.invoke({"job": job.model_dump(mode="json"), "input_version": run.input_version,
                              "report_key": key, "status": "ANALYZING"}, config)
            elif snapshot.next == ("await_retry",):
                graph.invoke(Command(resume={"action": "retry", "input_version": run.input_version}), config)
            elif snapshot.next and snapshot.next != ("await_review",):
                # Only explicit start/retry reaches this path after process interruption.
                graph.invoke(None, config)
        saved = self.manager.engine.get(key)
        from .role_analysis import AnalysisRun
        current = AnalysisRun.model_validate(saved)
        for name in AnalysisRun.model_fields:
            setattr(run, name, getattr(current, name))

    def status(self, job, version):
        with SqliteSaver.from_conn_string(self.path) as saver:
            state = self.graph(saver).get_state(self.config(job, version))
        return {"status": state.values.get("status", "NOT_STARTED"),
                "input_version": version, "next": list(state.next)}

    def decide(self, job, version, action):
        from .role_analysis import AnalysisRun
        manager = self.manager
        with manager.lock, manager.library.lock:
            if manager.worker and manager.worker.is_alive():
                raise RetrievalError("Wait for the active analysis to finish.", 409)
            if manager.environment(job)[0] != version:
                raise RetrievalError("The analysis changed. Review the current report first.", 409)
            key = manager.cache_key(job, version)
            saved = manager.engine.get(key)
            if not saved or saved["state"] != "COMPLETE":
                raise RetrievalError("Complete the analysis before reviewing it.", 409)
            if action == "accept":
                from .review_conversation import state as chat_state
                op = chat_state(manager, job).get("operation")
                if op and op["state"] in ("SAVING", "UPDATING", "PENDING"):
                    raise RetrievalError("Finish the pending review update before accepting the analysis.", 409)
            self.run(job, key, AnalysisRun.model_validate(saved))  # Adopt old completed reports without paid work.
            config = self.config(job, version)
            with SqliteSaver.from_conn_string(self.path) as saver:
                graph = self.graph(saver)
                snapshot = graph.get_state(config)
                if not snapshot.next:
                    if action == "accept" and snapshot.values.get("status") == "REVIEWED":
                        return self.status(job, version)
                    if action == "revise":
                        # Reopen a previously acknowledged report for a new revision.
                        graph.update_state(config, {"status": "AWAITING_REVIEW"}, as_node="assess_evidence")
                    else:
                        raise RetrievalError("A revision is pending for this report.", 409)
                graph.invoke(Command(resume={"action": action, "input_version": version}), config)
            if action == 'accept':
                reviewed = AnalysisRun.model_validate(manager.engine.get(key))
                for row in reviewed.results.values():
                    if row.judgment.assessment and row.judgment.assessment.review_required:
                        row.judgment.assessment.user_confirmed = True
                manager.save(key, reviewed)
            return self.status(job, version)
