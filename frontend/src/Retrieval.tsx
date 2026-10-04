import { useEffect, useRef, useState } from "react";
import { CandidateSkills } from "./CandidateSkills";

type Usage = { cap_usd: number | null; estimated_cost_usd: number; reserved_unknown_usd: number; calls: number };
type Source = { id: string; title: string; employer: string; project: string; source_class: string; category: "story" | "coaching"; revision: string | null; ready: boolean; state: string; error: string | null; preparation_error?: string; text?: string };
type Saved = { source: Source; preparation_error: string | null; duplicate?: boolean };
type Chunk = { id: string; heading: string; text: string; start_line: number; end_line: number; context?: string; source_title: string; source_path: string; source_class: string; employer: string; project: string; evidence_quote?: string; relevance_reason?: string; support?: string; limitation?: string; source_caveats?: string[] };
type Status = { sources: Source[]; source_sha256: string; ready: boolean; ready_count: number; source_count: number; configured: boolean; usage: Usage };
type Ranking = "hybrid" | "semantic" | "contextual_semantic" | "bm25";
type Results = { query: string; source_sha256: string; query_cached: boolean; rankings: Record<Ranking, Chunk[]>; searched_count: number; source_count: number; excluded_sources: string[]; matches: Chunk[]; no_match_reason: string; candidate_count: number; candidate_mode: string; rerank_cached: boolean };
type Draft = { title: string; employer: string; project: string; text: string };
const emptyDraft: Draft = { title: "", employer: "", project: "", text: "" };
const toDraft = (s: Source): Draft => ({ title: s.title, employer: s.employer, project: s.project, text: s.text ?? "" });

async function request<T>(path = "", body?: unknown): Promise<T> {
  const response = await fetch(`/api/library${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Local-Review": "1" }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Check the required fields and source size, then try again.");
  return value;
}

export function Passage({ chunk, position }: { chunk: Chunk; position: number }) {
  const text = chunk.text.replace(/^#{1,6}[^\n]*\r?\n/, "").trim();
  return <article className="retrieval-hit">
    <div className="eyebrow">MATCH {position} · {chunk.employer}</div><h3>{chunk.heading}</h3>
    <p><small>{chunk.source_title} · {chunk.project}{chunk.source_class === "COACHING_ANALYSIS_PENDING_USER_REVIEW" ? " · Coaching suggestion — unreviewed, not evidence" : chunk.source_class === "RAW_EVIDENCE" ? " · Raw notes / verify details" : " · Firsthand story, not approved claims"}</small></p>
    {chunk.relevance_reason && <p><strong>Why this matches:</strong> {chunk.relevance_reason}</p>}
    <p className="retrieval-excerpt">{chunk.evidence_quote ?? (text.length > 420 ? text.slice(0, 420) + "…" : text)}</p>
    {chunk.limitation && <p><strong>Still unclear:</strong> {chunk.limitation}</p>}
    {!!chunk.source_caveats?.length && <details><summary>Qualifications in the original passage</summary>{chunk.source_caveats.map((line,i) => <p key={i}>{line}</p>)}</details>}
    <details><summary>Read full passage</summary><pre className="retrieval-source">{chunk.text}</pre>
      <small>{chunk.source_path} · lines {chunk.start_line}–{chunk.end_line}</small></details>
  </article>;
}

export function Retrieval({ initialAdd = false }: { initialAdd?: boolean }) {
  const [intakeOpen, setIntakeOpen] = useState(initialAdd);
  const [intakeMode, setIntakeMode] = useState<"paste" | "upload">("upload");
  const [uploadEmployer, setUploadEmployer] = useState("");
  const [uploadProject, setUploadProject] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState("");
  const [consent, setConsent] = useState(false);
  const [category, setCategory] = useState<"story" | "coaching">("story");
  const [uploadCategory, setUploadCategory] = useState<"story" | "coaching">("story");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [replaceId, setReplaceId] = useState("");
  const [packStatus, setPackStatus] = useState("");
  const uploadId = useRef(crypto.randomUUID());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Results | null>(null);
  const [ranking, setRanking] = useState<Ranking>("hybrid");
  const [selected, setSelected] = useState<Source | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const lock = useRef(false);
  const sourceHash = useRef("");
  const saveId = useRef(crypto.randomUUID());
  const saveFingerprint = useRef("");
  const dirty = adding ? Object.values(draft).some(value => value.length > 0)
    : selected !== null && JSON.stringify(draft) !== JSON.stringify(toDraft(selected));

  async function refresh() {
    const next = await request<Status>();
    sourceHash.current = next.source_sha256;
    setStatus(next);
    setResults(previous => previous && previous.source_sha256 !== next.source_sha256 ? null : previous);
  }
  useEffect(() => { void refresh().catch(e => setError(e.message)); }, []);

  async function run(work: () => Promise<void>, message = "Checking stories…") {
    if (lock.current) return;
    lock.current = true; setBusy(true); setActivity(message); setError(""); setNotice("");
    try { await work(); }
    catch (e) { setError(e instanceof Error ? e.message : "Request failed."); }
    finally { await refresh().catch(() => {}); lock.current = false; setBusy(false); setActivity(""); }
  }
  async function openSource(id: string) {
    const source = await request<Source>(`/sources/${id}`);
    setSelected(source); setDraft(toDraft(source)); setAdding(false); setIntakeOpen(true); setIntakeMode("paste");
  }
  async function save() {
    const fingerprint = JSON.stringify(draft);
    if (saveFingerprint.current && saveFingerprint.current !== fingerprint) saveId.current = crypto.randomUUID();
    saveFingerprint.current = fingerprint;
    const result = adding
      ? await request<Saved>("/add-and-prepare", { ...draft, category: uploadCategory, request_id: saveId.current, consent_to_send: true })
      : await request<Saved>(`/sources/${selected!.id}/save-and-prepare`, { ...draft, expected_revision: selected!.revision, consent_to_send: true });
    showSaved(result);
  }

  function showSaved(result: Saved) {
    setSelected(result.source); setDraft(toDraft(result.source)); setAdding(false); setResults(null); setIntakeMode("paste");
    setNotice(result.preparation_error ? "Saved locally, but preparation stopped. Your document is safe; use Retry preparation after resolving the error."
      : result.source.ready ? (result.duplicate ? "This unchanged document is already ready. No new preparation was needed." : "Ready. Your document has been saved and prepared automatically.") : "Saved locally. Preparation is required.");
    if (result.preparation_error) setError(result.preparation_error);
  }
  async function upload() {
    if (!uploadFile) return;
    if (uploadFile.size > 2000000) throw new Error("Choose a file no larger than 2 MB.");
    const bytes = new Uint8Array(await uploadFile.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const replacement = status?.sources.find(s => s.id === replaceId);
    showSaved(await request<Saved>("/upload", { request_id: uploadId.current, filename: uploadFile.name,
      content_base64: btoa(binary), title: uploadTitle, employer: uploadEmployer, project: uploadProject, category: uploadCategory, consent_to_send: true,
      replace_source_id: replacement?.id ?? null, expected_revision: replacement?.revision ?? null }));
    setUploadFile(null); setUploadTitle(""); setUploadEmployer(""); setUploadProject("");
  }
  const scoped = status?.sources.filter(source => source.category === category) ?? [];

  return <section className="retrieval panel" aria-labelledby="retrieval-title">
    <h1 id="retrieval-title">Your context library</h1>
    {error && <div role="alert" className="error"><strong>The action did not complete.</strong><p>{error}</p></div>}
    {notice && <p role="status" className="notice">{notice}</p>}
    {busy && <p role="status" className="notice">{activity}</p>}
    {!status ? <><p>Checking your library…</p><button disabled={busy} onClick={() => void run(refresh)}>Check again</button></> : <>
      <section className="context-intake"><h2>Add your context</h2><p>Add your experience first. We’ll extract skills for you to review below.</p><button className="primary" aria-expanded={intakeOpen} onClick={()=>setIntakeOpen(!intakeOpen)}>Add experience or transcript</button>{intakeOpen&&<div className="context-intake-form">
        <p>Add context once to use across roles. Choose one employer/project per entry; split a transcript that covers several experiences into separate entries.</p>
        <div className="filters" aria-label="Add context method">
          <button disabled={busy || dirty} aria-pressed={intakeMode === "upload"} onClick={() => { setIntakeMode("upload"); setSelected(null); setAdding(false); }}>Upload document</button>
          <button disabled={busy || dirty} aria-pressed={intakeMode === "paste"} onClick={() => { setIntakeMode("paste"); setAdding(true); setSelected(null); setDraft(emptyDraft); saveId.current = crypto.randomUUID(); saveFingerprint.current = ""; }}>Paste experience or transcript</button>
        </div>
        <p>Saving prepares the new context for future role analyses. Existing reports become outdated and can be reanalyzed. New details remain unapproved evidence.</p>
        <p><small>Saving sends new or changed text to OpenAI for search embeddings and skill extraction. Coaching notes are excluded from skills. Original files and the search index stay local; unchanged text is reused.</small></p>
        {intakeMode === "upload" && <>
          <p>Word (.docx), text-based PDF, TXT or Markdown · up to 2 MB. Upload a written transcript; audio and video transcription is not supported.</p>
        <form onSubmit={e => { e.preventDefault(); void run(upload, "Saving and preparing your document… This may take a few minutes."); }}>
          <fieldset disabled={busy || dirty}>
            <label>Document type<select value={uploadCategory} onChange={e => { setUploadCategory(e.target.value as "story" | "coaching"); setReplaceId(""); uploadId.current = crypto.randomUUID(); }}><option value="story">Story</option><option value="coaching">Coaching guidance (unreviewed)</option></select></label>
            <label>Add or replace<select value={replaceId} onChange={e => { setReplaceId(e.target.value); uploadId.current = crypto.randomUUID(); }}><option value="">Add a new document</option>{status.sources.filter(s => s.category === uploadCategory && !s.error).map(s => <option key={s.id} value={s.id}>Replace {s.title} (keep history)</option>)}</select></label>
            <label>Choose document<input type="file" accept=".docx,.pdf,.txt,.md" required={!uploadFile} onChange={e => { const file = e.target.files?.[0] ?? null; setUploadFile(file); setUploadTitle(file?.name.replace(/\.[^.]+$/, "") ?? ""); uploadId.current = crypto.randomUUID(); }} /></label>
            <label>Document title<input required maxLength={160} value={uploadTitle} onChange={e => { setUploadTitle(e.target.value); uploadId.current = crypto.randomUUID(); }} /></label>
            {!replaceId && <div className="form-row">
              <label>Employer or initiative<input required maxLength={120} value={uploadEmployer} onChange={e => { setUploadEmployer(e.target.value); uploadId.current = crypto.randomUUID(); }} /></label>
              <label>Project<input required maxLength={160} value={uploadProject} onChange={e => { setUploadProject(e.target.value); uploadId.current = crypto.randomUUID(); }} /></label>
            </div>}
            <button className="primary" disabled={!uploadFile || !status.configured}>Upload and prepare</button>
          </fieldset>
        </form>

        </>}
        {intakeMode === "paste" && <>
        {(adding || selected) && <section className="retrieval-setup" aria-label="Story editor">
          <h2>{adding ? "New story" : selected!.title}</h2>{selected?.error && <p role="alert">{selected.error}</p>}
          <form onSubmit={event => { event.preventDefault(); void run(save, "Saving and automatically preparing your document…"); }}>
            <fieldset disabled={busy || Boolean(selected?.error)}>
              {adding && <label>Text type<select value={uploadCategory} onChange={e => setUploadCategory(e.target.value as "story" | "coaching")}><option value="story">Story</option><option value="coaching">Coaching guidance</option></select></label>}
              <label>Story title<input value={draft.title} required maxLength={160} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
              <div className="form-row">
                <label>Employer or initiative<input value={draft.employer} required maxLength={120} onChange={e => setDraft({ ...draft, employer: e.target.value })} /></label>
                <label>Project<input value={draft.project} required maxLength={160} onChange={e => setDraft({ ...draft, project: e.target.value })} /></label>
              </div>
              <label>Experience or transcript<textarea rows={12} value={draft.text} minLength={20} maxLength={64000} required onChange={e => setDraft({ ...draft, text: e.target.value })} /></label>
              <button type="submit" disabled={(!adding && !dirty) || !status.configured}>Save and prepare</button>
            </fieldset>
          </form>
          <button disabled={busy} onClick={() => { setSelected(null); setAdding(false); setDraft(emptyDraft); }}>Close without saving</button>
          {selected && !selected.error && <>{dirty ? <p>Save or close your edits before preparing this story.</p> : <>
            <p>{selected.ready ? "This version is ready. No preparation is needed." : "This version is not yet searchable."}</p>
            {selected.preparation_error && !selected.ready && <p role="alert">{selected.preparation_error}</p>}
            <button className="primary" disabled={busy || selected.ready || !status.configured} onClick={() => void run(async () => {
              setResults(null);
              await request(`/sources/${selected.id}/prepare`, { expected_revision: selected.revision, consent_to_send: true });
              await openSource(selected.id);
              setNotice("Story ready. You can search across all prepared stories now.");
            }, "Preparing search embeddings…")}>Retry preparation</button>
          </>}</>}
        </section>}

        </>}
      </div>}</section>
      <CandidateSkills />
      <h2>Search your documents</h2>
      <label>Document type<select value={category} disabled={busy} onChange={e => { setCategory(e.target.value as "story" | "coaching"); setResults(null); }}><option value="story">Experience stories</option><option value="coaching">Coaching notes</option></select></label>
      <p className="library-coverage">{scoped.filter(s => s.ready).length} documents available{scoped.some(s => !s.ready) ? ` · ${scoped.filter(s => !s.ready).length} need preparation` : ""}.</p>
      <form onSubmit={event => {
        event.preventDefault();
        if (!status.ready || !status.configured || !consent || query.trim().length < 3) return;
        void run(async () => { setResults(null); setResults(await request<Results>("/search", { query, category, consent_to_send: true })); }, "Searching your stories…");
      }}>
        <label>Your question<textarea rows={2} placeholder="e.g. Where have I influenced people without formal authority?" value={query} minLength={3} maxLength={1200} required disabled={busy} onChange={e => { setQuery(e.target.value); setResults(null); setError(""); }} /></label>
        <label className="retrieval-simple-consent"><input type="checkbox" checked={consent} disabled={busy} onChange={e => setConsent(e.target.checked)} />Allow OpenAI to embed this search question and Jev to check retrieved passages. Usage is tracked.</label>
        <button className="primary" disabled={busy || !status.ready || !consent || !status.configured || query.trim().length < 3}>{activity === "Searching your stories…" ? "Searching…" : "Find context"}</button>
      </form>
      {!status.ready && <p role="status">No stories are ready yet. Add context above, or open a saved document to retry preparation.</p>}
      {!status.configured && <p role="alert">The context library is not ready yet.</p>}
      {status.ready && !results && !busy && !error && <p role="status">{query.trim() ? "Question changed. Click Find context to search." : "Ready. Ask your question above."}</p>}
      {results && <section aria-label="Matching story passages" aria-live="polite">
        <h2>Search complete</h2><p>For: “{results.query}” · Searched {results.searched_count} of {results.source_count} documents.</p>
        <p>These passages were checked for relevance. Quotes are verified against the source; explanations are AI-generated, not approved career claims.</p>
        {results.matches.length === 0 ? <p role="status"><strong>No supported match found.</strong> {results.no_match_reason}</p>
          : results.matches.slice(0, 3).map((chunk, i) => <Passage key={chunk.id} chunk={chunk} position={i + 1} />)}
      </section>}
      <details className="retrieval-advanced"><summary>Saved documents · view or edit</summary>
        <p>Save and prepare creates API embeddings for new or changed text and stores the search index locally. Unchanged documents keep their index.</p>
        <p><small>Coaching stays unreviewed guidance. Neither indexing nor editing approves a resume claim.</small></p>
        <button disabled={busy || dirty} onClick={() => { setIntakeOpen(true); setIntakeMode("paste"); setAdding(true); setSelected(null); setDraft(emptyDraft); saveId.current = crypto.randomUUID(); saveFingerprint.current = ""; }}>+ Add text</button>
        <button disabled={busy} onClick={() => void run(refresh)}>Check for file updates</button>
        <div className="library-list">{status.sources.map(source => <div className="library-row" key={source.id}>
          <div><strong>{source.title}</strong><br /><small>{source.category === "coaching" ? "Coaching · Unreviewed" : source.employer} · {source.state}{source.source_class === "RAW_EVIDENCE" ? " · Raw notes" : ""}{source.preparation_error && !source.ready ? " · Preparation stopped" : ""}</small></div>
          <button disabled={busy || dirty} onClick={() => void run(() => openSource(source.id), "Opening story…")}>Open {source.title}</button>
        </div>)}</div>
      </details>
      <details className="retrieval-advanced"><summary>Master resume & always-loaded rules</summary>
        <p>The master resume, profile, approved facts, conflicts, corrections and protocols are loaded in full, not selected by similarity. This context pack is ready for the future tailoring step; tailoring is not yet connected. Older resumes remain reference-only.</p>
        <button disabled={busy} onClick={() => void run(async () => { const pack = await request<{ documents: unknown[]; reference_documents: unknown[] }>("/context-pack"); setPackStatus(`${pack.documents.length} current context documents and ${pack.reference_documents.length} separate historical references loaded locally. No model calls.`); }, "Checking the full context pack…")}>Verify context pack</button>
        {packStatus && <p role="status">{packStatus}</p>}
      </details>
      <details className="retrieval-advanced"><summary>Technical details & search comparisons</summary>
        <p>Original-text semantic search + BM25 retrieve candidates; a model then checks relevance against original passages. Small libraries check every current passage. New, changed, missing or removed sources are excluded until ready.</p>
        <p>Search uses original source text. Earlier generated summaries are excluded.</p>
        <p>No resume changes or approved claims. Generated context remains unapproved; raw notes may include unresolved questions.</p>
        <p><small>No local spending cap; usage is tracked. Responses use store=false; provider retention policies still apply. Local content, originals and revision history are unencrypted and may sync through OneDrive.</small></p>
        <p>${status.usage.estimated_cost_usd.toFixed(4)} estimated · ${status.usage.reserved_unknown_usd.toFixed(4)} reserved / unknown · {status.usage.calls} attempts. Not an invoice.</p>
        {results && <>
          <p>{results.query_cached ? "Query embedding reused." : "New query embedding saved."}</p>
          <p>{results.candidate_count} passages checked · {results.candidate_mode} · {results.rerank_cached ? "Saved relevance result reused." : "New relevance check."}</p>
          {results.excluded_sources.length > 0 && <p>Not searched: {results.excluded_sources.join("; ")}</p>}
          <p>Diagnostic candidates below are before relevance checking and may be wrong.</p>
          <div className="filters" aria-label="Compare retrieval methods">{([ ["hybrid", "Raw-text hybrid"], ["semantic", "Plain semantic"], ["bm25", "BM25"] ] as const).map(([key, label]) => <button key={key} aria-pressed={ranking === key} onClick={() => setRanking(key)}>{label}</button>)}</div>
          {results.rankings[ranking].map((chunk, i) => <Passage key={chunk.id} chunk={chunk} position={i + 1} />)}
        </>}
      </details>
    </>}
  </section>;
}
