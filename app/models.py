"""Milestone-one contracts, deliberately separate from verified production jobs."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def now() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Profile(Contract):
    id: str = "local-user"
    display_name: str = "Local user"
    preferences: list[str] = Field(default_factory=list)
    # A reusable placeholder, not an imported or verified career profile.
    mode: Literal["DEMO"] = "DEMO"


class ExtractionInput(Contract):
    url: str = Field(min_length=8, max_length=2048)


class ExtractionDraft(Contract):
    id: UUID
    original_url: HttpUrl
    final_url: HttpUrl
    fetched_url: HttpUrl
    retrieved_at: datetime
    adapter: Literal["GREENHOUSE_API_V1", "ASHBY_API_V1", "JSON_LD_V1"]
    company_source_url: HttpUrl | None = None
    company_response_sha256: str | None = None
    response_sha256: str
    company: str = Field(max_length=200)
    title: str = Field(max_length=200)
    description: str = Field(max_length=50000)
    warnings: list[str]


class JobInput(Contract):
    request_id: UUID
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=20, max_length=50000)
    source_url: HttpUrl | None = None
    extraction_id: UUID | None = None


class Job(JobInput):
    id: str
    profile_id: str
    created_at: datetime
    verification: Literal["UNVERIFIED"] = "UNVERIFIED"
    source_snapshot: ExtractionDraft | None = None
    extraction_confirmed_at: datetime | None = None


class FitAssessment(Contract):
    job_id: str
    version: int = Field(ge=1)
    created_at: datetime
    mode: Literal["SAMPLE_NOT_AI"] = "SAMPLE_NOT_AI"
    recommendation: Literal["REVIEW_SAMPLE"] = "REVIEW_SAMPLE"
    summary: str = "Sample only: no suitability, sponsorship, or career evidence has been evaluated."
    reasons: list[str] = Field(default_factory=lambda: [
        "This fixed sample exercises the approval workflow; it is not a hiring recommendation.",
        "Approving records your review only. It does not tailor a resume or apply to a role.",
    ])
    fact_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class VersionInput(Contract):
    expected_version: int = Field(ge=1, strict=True)


class DecisionInput(VersionInput):
    decision: Literal["APPROVE", "SKIP"]


class Approval(Contract):
    job_id: str
    assessment_version: int = Field(ge=1)
    decision: Literal["APPROVE", "SKIP"]
    actor: str = "local-user"
    created_at: datetime
    scope: Literal["SAMPLE_FIT_REVIEW_ONLY"] = "SAMPLE_FIT_REVIEW_ONLY"


class Review(Contract):
    job: Job
    assessment: FitAssessment
    approval: Approval | None
    history: list[Approval]
    status: Literal["AWAITING_REVIEW", "REVIEW_APPROVED", "SKIPPED"]
    workflow_status: Literal["PAUSED_FOR_REVIEW", "COMPLETE", "RECOVERY_PENDING"]
