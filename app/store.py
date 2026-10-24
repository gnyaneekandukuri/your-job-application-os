"""Durable business records. SQLite constraints enforce versioned, unique decisions."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import Approval, DecisionInput, ExtractionDraft, FitAssessment, Job, JobInput, Profile, now


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("Unsupported database version; do not open a newer database with this app.")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    input TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS assessments (
                    job_id TEXT NOT NULL REFERENCES jobs(id), version INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(job_id, version));
                CREATE TABLE IF NOT EXISTS approvals (
                    job_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(job_id, version),
                    FOREIGN KEY(job_id, version) REFERENCES assessments(job_id, version));
                CREATE TABLE IF NOT EXISTS extractions (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                PRAGMA user_version=2;
            """)
            profile = Profile()
            db.execute("INSERT OR IGNORE INTO profiles VALUES (?, ?)",
                       (profile.id, profile.model_dump_json()))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def profile(self):
        with self.connection() as db:
            return Profile.model_validate_json(db.execute("SELECT payload FROM profiles").fetchone()[0])

    def ids(self):
        with self.connection() as db:
            return [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY rowid DESC")]

    def save_extraction(self, draft: ExtractionDraft):
        with self.connection() as db:
            db.execute("INSERT INTO extractions VALUES (?, ?)", (str(draft.id), draft.model_dump_json()))

    def extraction(self, extraction_id: str):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM extractions WHERE id=?", (extraction_id,)).fetchone()
            if not row:
                raise Missing("Extracted draft not found. Extract the link again.")
            return ExtractionDraft.model_validate_json(row[0])

    def create(self, data: JobInput) -> str:
        serialized = data.model_dump_json()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id, input FROM jobs WHERE request_id=?",
                                  (str(data.request_id),)).fetchone()
            if existing:
                if JobInput.model_validate_json(existing["input"]) != data:
                    raise Conflict("This request ID belongs to different job content. Start a new entry.")
                return existing["id"]
            snapshot = None
            if data.extraction_id:
                row = db.execute("SELECT payload FROM extractions WHERE id=?", (str(data.extraction_id),)).fetchone()
                if not row:
                    raise Missing("Extracted draft not found. Extract the link again.")
                snapshot = ExtractionDraft.model_validate_json(row[0])
                if data.source_url != snapshot.original_url:
                    raise Conflict("The source link no longer matches the extracted draft. Extract the new link before saving.")
            job = Job(**data.model_dump(), id=str(uuid4()), profile_id="local-user", created_at=now(),
                      source_snapshot=snapshot, extraction_confirmed_at=now() if snapshot else None)
            assessment = FitAssessment(job_id=job.id, version=1, created_at=now())
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, 1)",
                       (job.id, str(data.request_id), serialized, job.model_dump_json()))
            db.execute("INSERT INTO assessments VALUES (?, 1, ?)", (job.id, assessment.model_dump_json()))
            return job.id

    def read_jobs(self, job_ids):
        """Read job content for list enrichment without loading approval histories."""
        result = {}
        ids = list(dict.fromkeys(job_ids))
        with self.connection() as db:
            for start in range(0, len(ids), 500):
                group = ids[start:start+500]
                for row in db.execute('SELECT id,payload FROM jobs WHERE id IN ('+','.join('?' for _ in group)+')', group):
                    result[row['id']] = Job.model_validate_json(row['payload'])
        return result

    def read(self, job_id: str):
        with self.connection() as db:
            db.execute("BEGIN")
            job = db.execute("SELECT payload, version FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise Missing("Job not found.")
            assessment = db.execute("SELECT payload FROM assessments WHERE job_id=? AND version=?",
                                    (job_id, job["version"])).fetchone()
            history = [Approval.model_validate_json(r[0]) for r in db.execute(
                "SELECT payload FROM approvals WHERE job_id=? ORDER BY version", (job_id,))]
            approval = next((a for a in history if a.assessment_version == job["version"]), None)
            return (Job.model_validate_json(job["payload"]),
                    FitAssessment.model_validate_json(assessment[0]), approval, history)

    def decide(self, job_id: str, data: DecisionInput):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise Missing("Job not found.")
            if row[0] != data.expected_version:
                raise Conflict("Assessment changed. Reload and review the current version.")
            existing = db.execute("SELECT payload FROM approvals WHERE job_id=? AND version=?",
                                  (job_id, data.expected_version)).fetchone()
            if existing:
                prior = Approval.model_validate_json(existing[0])
                if prior.decision != data.decision:
                    raise Conflict("This version already has a different decision. Create a new sample version to review again.")
                return prior
            approval = Approval(job_id=job_id, assessment_version=data.expected_version,
                                decision=data.decision, created_at=now())
            db.execute("INSERT INTO approvals VALUES (?, ?, ?)",
                       (job_id, data.expected_version, approval.model_dump_json()))
            return approval

    def revise(self, job_id: str, expected_version: int):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise Missing("Job not found.")
            if row[0] != expected_version:
                raise Conflict("Assessment changed. Reload before creating another version.")
            version = expected_version + 1
            assessment = FitAssessment(job_id=job_id, version=version, created_at=now())
            db.execute("INSERT INTO assessments VALUES (?, ?, ?)",
                       (job_id, version, assessment.model_dump_json()))
            db.execute("UPDATE jobs SET version=? WHERE id=?", (version, job_id))
