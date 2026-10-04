import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Usage } from "./Usage";
import { AssessmentResults } from "./AssessmentResults";
import { Setup } from "./Setup";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import type { Decision, Review, ExtractionDraft } from "./types";
import "./style.css";
import { Retrieval } from "./Retrieval";
import { Discovery } from "./Discovery";
import { Requirements } from "./Requirements";

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Local-Review": "1",
          },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map(
                (e: { loc: string[]; msg: string }) =>
                  `${e.loc.slice(1).join(".")}: ${e.msg}`,
              )
              .join("; ")
          : `Request failed (${response.status}). Please retry.`;
    throw new ApiError(message, response.status);
  }
  return response.json();
}

const labels = {
  AWAITING_REVIEW: "Needs your review",
  REVIEW_APPROVED: "Review approved",
  SKIPPED: "Skipped",
};

function App() {
  const [addContext, setAddContext] = useState(false);
  const [opportunityToReview,setOpportunityToReview]=useState<string|null>(null);
  const [assessmentTab,setAssessmentTab]=useState("worth");
  const [page, setPage] = useState<"queue" | "retrieval" | "discovery" | "discovery-runs" | "assessments" | "usage">(window.location.hash === "#usage" ? "usage" : window.location.hash === "#context" ? "retrieval" : "discovery");
  const [jobs, setJobs] = useState<Review[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [roleOpen, setRoleOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("all");
  const [entryMode, setEntryMode] = useState<"link" | "manual">("link");
  const [link, setLink] = useState("");
  const [extraction, setExtraction] = useState<ExtractionDraft | null>(null);
  const [draft, setDraft] = useState({
    company: "",
    title: "",
    description: "",
    source_url: "",
  });
  const requestId = useRef(crypto.randomUUID());
  const requestFingerprint = useRef("");
  const actionLock = useRef(false);
  const review = jobs.find((j) => j.job.id === selected);

  async function refresh() {
    const rows = await api<Review[]>("/jobs");
    setJobs(rows);
    setSelected((current) =>
      rows.some((r) => r.job.id === current)
        ? current
        : (rows[0]?.job.id ?? null),
    );
  }
  useEffect(() => {
    if (page !== "queue" || roleOpen) return;
    setLoading(true);
    refresh()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [page, roleOpen]);

  function replace(row: Review) {
    setJobs((current) => [
      row,
      ...current.filter((r) => r.job.id !== row.job.id),
    ]);
    setSelected(row.job.id);
    setCreating(false);
    setRoleOpen(true);
  }

  async function action(work: () => Promise<void>) {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Something went wrong. Please retry.",
      );
      if (e instanceof ApiError && e.status === 409)
        await refresh().catch(() => {});
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      const content = {
        ...draft,
        source_url: draft.source_url.trim() || null,
        extraction_id: extraction?.id ?? null,
      };
      const fingerprint = JSON.stringify(content);
      if (
        requestFingerprint.current &&
        requestFingerprint.current !== fingerprint
      )
        requestId.current = crypto.randomUUID();
      requestFingerprint.current = fingerprint;
      const row = await api<Review>("/jobs", {
        ...content,
        request_id: requestId.current,
      });
      replace(row);
      setDraft({ company: "", title: "", description: "", source_url: "" });
      setExtraction(null);
      setLink("");
      setEntryMode("link");
      requestId.current = crypto.randomUUID();
      requestFingerprint.current = "";
      await startAnalysis(row.job.id);
    });
  }

  function extract(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      setNotice("Extracting job details…");
      setExtraction(null);
      setDraft({
        company: "",
        title: "",
        description: "",
        source_url: link.trim(),
      });
      const result = await api<ExtractionDraft>("/extractions", {
        url: link.trim(),
      });
      setExtraction(result);
      setDraft({
        company: result.company,
        title: result.title,
        description: result.description,
        source_url: result.original_url,
      });
      if (!result.company.trim() || !result.title.trim() || result.description.trim().length < 20) {
        setNotice("Some details are missing. Correct them below to continue.");
        return;
      }
      setNotice("Saving the job and starting analysis…");
      const row = await api<Review>("/jobs", {
        company: result.company, title: result.title, description: result.description,
        source_url: result.original_url, extraction_id: result.id, request_id: requestId.current,
      });
      replace(row);
      requestId.current = crypto.randomUUID();
      setExtraction(null);
      setLink("");
      await startAnalysis(row.job.id);
    });
  }

  async function startAnalysis(jobId: string) {
    setNotice("Job saved. Starting requirement extraction and evidence matching…");
    const preview = await api<{ input_version: string }>(`/jobs/${jobId}/analysis`);
    await api(`/jobs/${jobId}/analysis`, { consent_to_send: true, input_version: preview.input_version });
    setNotice("Analysis started. All requirements will be matched automatically below.");
  }

  function decide(decision: Decision) {
    if (!review) return;
    void action(async () => {
      replace(
        await api<Review>(`/jobs/${review.job.id}/decision`, {
          expected_version: review.assessment.version,
          decision,
        }),
      );
      setNotice(
        decision === "APPROVE"
          ? "Review approved and saved. No application was submitted."
          : "Skipped and saved.",
      );
    });
  }

  const pending = jobs.filter((j) => !j.approval).length;
  const visible = jobs.filter(
    (j) =>
      filter === "all" || (filter === "pending" ? !j.approval : !!j.approval),
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Job Review home">
          <span className="mark">jr</span> Job Review
        </a>
        <div className="workspace-label">PERSONAL WORKSPACE</div>
        <div className="nav-active">
          Review workspace
        </div>
        <div className="sidebar-note">
          <span className="dot" /> Local workspace
          <br />
          <small>Single user · private storage</small>
        </div>
        <div className="milestone">
          <small>WHOLE-ROLE EVIDENCE</small>
          <strong>Find your next role.</strong>
          <p>
            Discover → assess → decide.
            <br />
            Choose jobs, then prepare and apply.
          </p>
        </div>
      </aside>
      <main>
        <nav className="filters" aria-label="Workspace views">
          <button aria-pressed={page === "assessments" || page === "discovery"} onClick={() => {setOpportunityToReview(null);setPage("discovery");}}>Your jobs</button>
          <button aria-pressed={page === "queue"} onClick={() => { setPage("queue"); setRoleOpen(false); setCreating(false); }}>Saved roles</button>
          <button aria-pressed={page === "usage"} onClick={() => setPage("usage")}>API costs</button>
          <button aria-pressed={page === "discovery-runs"} onClick={() => setPage("discovery-runs")}>Discovery runs</button>
          <button aria-pressed={page === "retrieval"} onClick={() => { setAddContext(false); setPage("retrieval"); }}>Your context</button>
        </nav>
        {(page==='discovery'||page==='assessments')&&<header className="jobs-heading"><h1>Your jobs</h1><p>Find a job → Understand the fit → Decide whether to tailor.</p><nav className="decision-tabs" aria-label="Job stages"><button aria-pressed={page==='discovery'} onClick={()=>{setOpportunityToReview(null);setPage('discovery');}}>New jobs</button><button aria-pressed={page==='assessments'} onClick={()=>{setAssessmentTab('worth');setPage('assessments');}}>Analyzed jobs</button></nav></header>}
        {page === "usage" ? <Usage /> : page === "assessments" ? <AssessmentResults onUpdatedJD={id=>{setOpportunityToReview(id);setPage("discovery");}} key={assessmentTab} onDiscover={()=>{setOpportunityToReview(null);setPage("discovery");}} initialTab={assessmentTab} onOpen={async (id) => { replace(await api<Review>(`/jobs/${id}`)); setLoading(false); setPage("queue"); }} /> : page === "discovery" || page === "discovery-runs" ? <Discovery initialReviewId={opportunityToReview} onResults={(tab="worth") => {setAssessmentTab(tab);setPage("assessments");}} view={page === "discovery-runs" ? "runs" : "opportunities"} onOpen={async (id) => { replace(await api<Review>(`/jobs/${id}`)); setLoading(false); setPage("queue"); }} /> : page === "retrieval" ? <Retrieval initialAdd={addContext} /> : <>
        {roleOpen && <button className="text-action" onClick={()=>{setAssessmentTab("worth");setPage("assessments");}}>← Back to analyzed jobs</button>}
        <header>
          <div>
            <div className="eyebrow">YOUR JOB SEARCH, ONE STEP AT A TIME</div>
            <h1>{creating ? "Add a role" : roleOpen ? "Role workspace" : "Saved roles"}</h1>
            <p>{roleOpen && !creating ? "Review the evidence and recommendation, then decide whether to prepare an application or pass." : "Choose a saved role to open its analysis, or add a new link."}</p>
          </div>
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              setCreating(true);
              setError("");
              setNotice("");
            }}
          >
            + Add a role
          </button>
        </header>
        {!roleOpen && !creating && <section className="panel context-entry" aria-label="Build your context">
          <div><strong>Your experience powers every analysis</strong><p>Add stories, project notes or written transcripts before choosing a role.</p></div>
          <button disabled={busy} onClick={() => { setAddContext(true); setPage("retrieval"); }}>+ Add experience or transcript</button>
        </section>}
        <div className="demo-banner">
          <strong>Evidence review</strong>
          <span>
            Paste a link → extract and analyze → review your evidence.
            No resume changes or applications happen automatically.
          </span>
        </div>
        {!roleOpen && !creating && <div className="stats">
          <div>
            <strong>{jobs.length}</strong>
            <span>Roles saved</span>
          </div>
          <div>
            <strong>{pending}</strong>
            <span>Without a sample decision</span>
          </div>
          <div>
            <strong>{jobs.length - pending}</strong>
            <span>Legacy sample decisions</span>
          </div>
        </div>}
        {error && (
          <div className="error" role="alert">
            {error}{" "}
            <button disabled={busy} onClick={() => void action(refresh)}>
              Reload saved jobs
            </button>
          </div>
        )}
        {notice && (
          <div className="notice" role="status">
            {notice}
          </div>
        )}
        {loading ? (
          <p role="status">Loading your local workspace…</p>
        ) : creating ? (
          <section className="entry panel">
            <div className="section-heading">
              <div>
                <div className="eyebrow">START WITH ONE ROLE</div>
                <h2>Add a role</h2>
              </div>
              <button disabled={busy} onClick={() => setCreating(false)}>
                Cancel
              </button>
            </div>
            <div className="filters intake-tabs" aria-label="Job entry method">
              <button
                disabled={busy}
                aria-pressed={entryMode === "link"}
                onClick={() => {
                  setEntryMode("link");
                  setError("");
                }}
              >
                From a link
              </button>
              <button
                disabled={busy}
                aria-pressed={entryMode === "manual"}
                onClick={() => {
                  setEntryMode("manual");
                  setExtraction(null);
                  setError("");
                  setNotice("");
                }}
              >
                Enter manually
              </button>
            </div>
            {entryMode === "link" && (
              <>
                <p>
                  Paste a public job or application link. We support Ashby and
                  Greenhouse listings and pages with structured JobPosting data.
                  Login-protected or unsupported pages can be entered manually.
                </p>
                <form onSubmit={extract}>
                  <fieldset disabled={busy}>
                    <label>
                      Job or application link
                      <input
                        autoFocus
                        required
                        type="url"
                        maxLength={2048}
                        value={link}
                        onChange={(e) => {
                          setLink(e.target.value);
                          setExtraction(null);
                          setDraft({
                            company: "",
                            title: "",
                            description: "",
                            source_url: e.target.value,
                          });
                          setNotice("");
                        }}
                        placeholder="https://job-boards.greenhouse.io/company/jobs/…"
                      />
                    </label>
                    <div className="form-footer">
                      <span>
                        Saves the extracted job and starts Jev assessment using
                        the JD, your stories and fact/conflict rules with usage tracking. No application is submitted.
                      </span>
                      <button className="primary" type="submit">
                        {busy ? "Working…" : "Extract & analyze role"}
                      </button>
                    </div>
                  </fieldset>
                </form>
              </>
            )}
            {extraction && (
              <section
                className="extraction-summary"
                aria-label="Extraction result"
              >
                <h3>Check the extracted details</h3>
                <p>
                  {extraction.adapter === "GREENHOUSE_API_V1"
                    ? "Greenhouse public API"
                    : extraction.adapter === "ASHBY_API_V1"
                      ? "Ashby public API"
                      : "Structured JobPosting data"}{" "}
                  · Retrieved{" "}
                  {new Date(extraction.retrieved_at).toLocaleString()}
                </p>
                <ul>
                  {extraction.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
                <p>
                  Nothing is in your queue yet. Confirming below saves your
                  corrections and preserves the original extracted text.
                </p>
              </section>
            )}
            {(entryMode === "manual" || extraction) && (
              <form onSubmit={submit}>
                <fieldset disabled={busy}>
                  <div className="form-row">
                    <label>
                      Company
                      <input
                        required
                        maxLength={200}
                        value={draft.company}
                        onChange={(e) =>
                          setDraft({ ...draft, company: e.target.value })
                        }
                        placeholder="e.g. Example Labs"
                      />
                    </label>
                    <label>
                      Role title
                      <input
                        required
                        maxLength={200}
                        value={draft.title}
                        onChange={(e) =>
                          setDraft({ ...draft, title: e.target.value })
                        }
                        placeholder="e.g. AI Product Manager"
                      />
                    </label>
                  </div>
                  <label>
                    Listing URL <span className="muted">(optional)</span>
                    <input
                      type="url"
                      readOnly={!!extraction}
                      value={draft.source_url}
                      onChange={(e) =>
                        setDraft({ ...draft, source_url: e.target.value })
                      }
                      placeholder="https://…"
                    />
                  </label>
                  <label>
                    Job description
                    <textarea
                      required
                      minLength={20}
                      maxLength={50000}
                      rows={9}
                      value={draft.description}
                      onChange={(e) =>
                        setDraft({ ...draft, description: e.target.value })
                      }
                      placeholder="Paste at least 20 characters of the job description…"
                    />
                  </label>
                  <div className="form-footer">
                    <span>
                      Saves this job and starts Jev assessment using the JD, retrieved stories
                      and fact/conflict rules with usage tracked.
                    </span>
                    <button className="primary" type="submit">
                      {busy
                        ? "Saving…"
                        : "Save & analyze role"}
                    </button>
                  </div>
                </fieldset>
              </form>
            )}
          </section>
        ) : jobs.length === 0 ? (
          <section className="empty panel">
            <div className="empty-glyph">01</div>
            <h2>Your first role starts here.</h2>
            <p>
              Paste a job link to extract the details and try the review loop.
              <br />
              Your decision will still be here when you return.
            </p>
            <button className="primary" onClick={() => setCreating(true)}>
              Add your first role
            </button>
            <small>AI analysis requires an API key. No applications submitted.</small>
          </section>
        ) : (
          <div className="review-layout">
            {!roleOpen && <section className="job-list all-roles-list" aria-label="Saved roles">
              <div className="filters" aria-label="Filter roles">
                {[
                  ["all", "All roles"],
                  ["pending", "To review"],
                  ["done", "Reviewed"],
                ].map(([value, text]) => (
                  <button
                    key={value}
                    aria-pressed={filter === value}
                    onClick={() => setFilter(value)}
                  >
                    {text}
                  </button>
                ))}
              </div>
              {visible.length === 0 && (
                <p className="muted">No roles in this view.</p>
              )}
              {visible.map((item) => (
                <button
                  disabled={busy}
                  key={item.job.id}
                  className={`job-card ${selected === item.job.id ? "selected" : ""}`}
                  onClick={() => {
                    setSelected(item.job.id);
                    setRoleOpen(true);
                    setNotice("");
                  }}
                >
                  <span className="company">{item.job.company}</span>
                  <strong>{item.job.title}</strong>
                  <span className={`badge ${item.approval ? "done" : ""}`}>
                    {labels[item.status]}
                  </span>
                  <small>Open role & analysis →</small>
                </button>
              ))}
            </section>}
            {roleOpen && review && (
              <article className="review panel">
                <button className="back-to-roles" onClick={() => { setRoleOpen(false); setNotice(""); setError(""); }}>← Back to all roles</button>
                <div className="section-heading">
                  <div>
                    <div className="eyebrow">{review.job.company}</div>
                    <h2>{review.job.title}</h2>
                  </div>
                  <span className="version">v{review.assessment.version}</span>
                </div>
                <div className="detail-meta">
                  <span>Unverified listing</span>
                  <span>
                    Added {new Date(review.job.created_at).toLocaleDateString()}
                  </span>
                </div>
                <Requirements key={review.job.id} jobId={review.job.id} />
                <details><summary>Legacy sample assessment (not this analysis)</summary><div className="assessment">
                  <div className="eyebrow">
                    SAMPLE ASSESSMENT · NOT AI-GENERATED
                  </div>
                  <h3>A practice review, not a fit score.</h3>
                  <p>{review.assessment.summary}</p>
                  <ul>
                    {review.assessment.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                  <div className="evidence">
                    Evidence: none used in sample mode
                  </div>
                </div></details>
                <details>
                  <summary>Read the saved job description</summary>
                  <p className="description">{review.job.description}</p>
                  {review.job.source_url && (
                    <p className="source">
                      Source:{" "}
                      <a
                        href={review.job.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {review.job.source_url}
                      </a>
                    </p>
                  )}
                </details>
                {review.job.source_snapshot && (
                  <details className="technical">
                    <summary>Extraction source & original text</summary>
                    {review.job.source_snapshot.company_source_url && (
                      <p className="source">
                        Company name source:{" "}
                        {review.job.source_snapshot.company_source_url}
                      </p>
                    )}
                    <p>
                      Retrieved{" "}
                      {new Date(
                        review.job.source_snapshot.retrieved_at,
                      ).toLocaleString()}{" "}
                      using {review.job.source_snapshot.adapter}. Extracted
                      details saved{" "}
                      {review.job.extraction_confirmed_at
                        ? new Date(
                            review.job.extraction_confirmed_at,
                          ).toLocaleString()
                        : "—"}
                      .
                    </p>
                    <p className="source">
                      Original link: {review.job.source_snapshot.original_url}
                      <br />
                      Listing destination:{" "}
                      {review.job.source_snapshot.final_url}
                      <br />
                      Fetched data: {review.job.source_snapshot.fetched_url}
                    </p>
                    <p>
                      Original company:{" "}
                      {review.job.source_snapshot.company || "Missing"}
                      <br />
                      Original title:{" "}
                      {review.job.source_snapshot.title || "Missing"}
                    </p>
                    <ul>
                      {review.job.source_snapshot.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                    <p className="description">
                      {review.job.source_snapshot.description ||
                        "No description extracted."}
                    </p>
                  </details>
                )}
                <details><summary>Legacy sample approval workflow</summary>
                {review.workflow_status === "RECOVERY_PENDING" && (
                  <div role="alert" className="error">
                    Your record is saved, but workflow recovery is pending.
                    Reload to retry.
                  </div>
                )}
                {!review.approval ? (
                  <section className="decision">
                    <h3>Your call.</h3>
                    <p>
                      Approve this sample review or skip the role. Neither
                      action applies to the job.
                    </p>
                    <div className="actions">
                      <button
                        className="primary"
                        disabled={
                          busy || review.workflow_status === "RECOVERY_PENDING"
                        }
                        onClick={() => decide("APPROVE")}
                      >
                        Approve sample review
                      </button>
                      <button
                        disabled={
                          busy || review.workflow_status === "RECOVERY_PENDING"
                        }
                        onClick={() => decide("SKIP")}
                      >
                        Skip role
                      </button>
                    </div>
                    <small>
                      Decision applies only to assessment v
                      {review.assessment.version}.
                    </small>
                  </section>
                ) : (
                  <section className="decision saved">
                    <h3>{labels[review.status]}</h3>
                    <p>
                      Saved{" "}
                      {new Date(review.approval.created_at).toLocaleString()}.
                      No application was submitted.
                    </p>
                  </section>
                )}
                <details className="technical">
                  <summary>Version history & workflow</summary>
                  <p>
                    LangGraph:{" "}
                    {review.workflow_status.replaceAll("_", " ").toLowerCase()}.
                    Decisions are stored in SQLite.
                  </p>
                  <p>
                    Creating a new sample version keeps the same fixed content
                    but requires a fresh decision. Earlier decisions stay in
                    history.
                  </p>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        replace(
                          await api<Review>(
                            `/jobs/${review.job.id}/sample-version`,
                            { expected_version: review.assessment.version },
                          ),
                        );
                        setNotice(
                          "New sample version saved. A fresh review is required.",
                        );
                      })
                    }
                  >
                    Create new sample version
                  </button>
                  <ul>
                    {review.history.map((a) => (
                      <li key={a.assessment_version}>
                        v{a.assessment_version} ·{" "}
                        {a.decision === "APPROVE" ? "Approved" : "Skipped"} ·{" "}
                        {new Date(a.created_at).toLocaleString()}
                      </li>
                    ))}
                  </ul>
                </details>
                </details>
              </article>
            )}
          </div>
        )}
        </>}
        <footer>
          LOCAL-FIRST PROTOTYPE <span>Human review stays in the loop.</span>
        </footer>
      </main>
    </div>
  );
}

const queryClient = new QueryClient({defaultOptions:{queries:{retry:1,staleTime:15000,refetchOnWindowFocus:true}}});
createRoot(document.getElementById("root")!).render(<QueryClientProvider client={queryClient}><Setup><App /></Setup></QueryClientProvider>);
