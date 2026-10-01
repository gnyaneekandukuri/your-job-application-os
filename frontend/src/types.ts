// API response contracts; Pydantic validates the same shapes at the server boundary.
export type Decision = "APPROVE" | "SKIP";
export type ExtractionDraft = {
  id: string;
  original_url: string;
  final_url: string;
  fetched_url: string;
  retrieved_at: string;
  adapter: "GREENHOUSE_API_V1" | "ASHBY_API_V1" | "JSON_LD_V1";
  company_source_url: string | null;
  company_response_sha256: string | null;
  response_sha256: string;
  company: string;
  title: string;
  description: string;
  warnings: string[];
};
export type Approval = {
  job_id: string;
  assessment_version: number;
  decision: Decision;
  actor: string;
  created_at: string;
  scope: "SAMPLE_FIT_REVIEW_ONLY";
};
export type Review = {
  job: {
    id: string;
    company: string;
    title: string;
    description: string;
    source_url: string | null;
    created_at: string;
    verification: "UNVERIFIED";
    source_snapshot: ExtractionDraft | null;
    extraction_confirmed_at: string | null;
  };
  assessment: {
    version: number;
    summary: string;
    reasons: string[];
    mode: "SAMPLE_NOT_AI";
  };
  approval: Approval | null;
  history: Approval[];
  status: "AWAITING_REVIEW" | "REVIEW_APPROVED" | "SKIPPED";
  workflow_status: "PAUSED_FOR_REVIEW" | "COMPLETE" | "RECOVERY_PENDING";
};
