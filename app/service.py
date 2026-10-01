import logging
from threading import RLock

from langgraph.types import Command
from langsmith import tracing_context

from .models import Review


class ReviewService:
    """Single-process service; business records are authoritative over graph checkpoints.

    A crash between the two database commits is repaired from the saved decision.
    The graph has no side effects, making this reconciliation safe to replay.
    """
    def __init__(self, store, graph):
        self.store, self.graph = store, graph
        self.lock = RLock()

    def reconcile(self, job_id):
        with self.lock, tracing_context(enabled=False):
            _, assessment, approval, _ = self.store.read(job_id)
            config = {"configurable": {"thread_id": f"{job_id}:v{assessment.version}"}}
            snapshot = self.graph.get_state(config)
            if not snapshot.values:
                self.graph.invoke({"assessment": assessment.model_dump(mode="json"),
                                   "approval": None, "status": "CREATED"}, config)
            elif snapshot.next and not snapshot.interrupts:
                self.graph.invoke(None, config)
            snapshot = self.graph.get_state(config)
            if approval and snapshot.next:
                self.graph.invoke(Command(resume=approval.model_dump(mode="json")), config)
            snapshot = self.graph.get_state(config)
            if approval:
                if snapshot.next or snapshot.values.get("approval") != approval.model_dump(mode="json"):
                    raise RuntimeError("Checkpoint and saved approval disagree")
                return "COMPLETE"
            if not snapshot.next or not snapshot.interrupts:
                raise RuntimeError("Expected a pending human-review checkpoint")
            return "PAUSED_FOR_REVIEW"

    def review(self, job_id):
        with self.lock:
            try:
                workflow_status = self.reconcile(job_id)
            except Exception:
                # Preserve the ledger even if checkpointing fails; don't report an unsaved decision.
                logging.getLogger(__name__).warning("Workflow reconciliation pending for job %s", job_id)
                workflow_status = "RECOVERY_PENDING"
            job, assessment, approval, history = self.store.read(job_id)
            status = "AWAITING_REVIEW" if not approval else (
                "REVIEW_APPROVED" if approval.decision == "APPROVE" else "SKIPPED")
            return Review(job=job, assessment=assessment, approval=approval, history=history,
                          status=status, workflow_status=workflow_status)

    def create(self, data):
        with self.lock:
            return self.review(self.store.create(data))

    def decide(self, job_id, data):
        with self.lock:
            self.store.decide(job_id, data)
            return self.review(job_id)

    def revise(self, job_id, data):
        with self.lock:
            self.store.revise(job_id, data.expected_version)
            return self.review(job_id)
