import { useEffect, useRef, useState } from "react";

type EvidenceState = { context_version: string; ready: boolean; unready_sources?: string[]; result: null | {
  judgment: { support: string; explanation: string; limitation: string; clarification: string; fact_ids: string[] };
  passages: { passage_id: string; quote: string; source_title: string; source_path: string; source_class: string;
    employer: string; start_line: number; end_line: number; text: string; source_caveats: string[] }[];
  searched_count: number; source_count: number;
}; usage: { cap_usd: number | null; estimated_cost_usd: number } };

export function Evidence({ jobId, requirementId }: { jobId: string; requirementId: string }) {
  const [state, setState] = useState<EvidenceState | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true), lock = useRef(false);
  const path = `/api/jobs/${jobId}/requirements/${requirementId}/evidence`;
  useEffect(() => {
    let cancelled = false; alive.current = true;
    fetch(path).then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail); return data; })
      .then(data => { if (!cancelled) setState(data); }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; alive.current = false; };
  }, [path]);
  async function check() {
    if (!state || !consent || lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Local-Review": "1" },
        body: JSON.stringify({ consent_to_send: true, context_version: state.context_version }) });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Evidence check failed.");
      if (alive.current) setState(data);
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "Evidence check failed."); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  const labels: Record<string, string> = { direct: "Direct evidence", transferable: "Transferable evidence", unclear: "Needs clarification", not_documented: "Not documented in retrieved stories" };
  return <section aria-label="Requirement evidence" className="extraction-summary">
    <h4>Your experience evidence</h4>
    <p>Story evidence only. This does not approve facts or recommend applying.</p>
    {error && <p role="alert" className="error">{error} Close and reopen this evidence panel to reload current sources.</p>}
    {!state && !error && <p role="status">Loading current evidence…</p>}
    {state && !state.ready && <p role="alert">Prepare these stories first: {state.unready_sources?.join(", ") || "No stories available"}.</p>}
    {state && !state.result && <>
      <label><input type="checkbox" disabled={busy} checked={consent} onChange={e => setConsent(e.target.checked)} /> Load this requirement’s saved Jev assessment. Use the role assessment to complete unfinished checks.</label>
      <p className="muted">Up to three calls: query embedding, passage relevance, evidence interpretation. Cached steps are reused. No local spending cap; estimated spend ${state.usage.estimated_cost_usd.toFixed(2)}.</p>
      <button disabled={!consent || !state.ready || busy} onClick={() => void check()}>{busy ? "Checking evidence…" : "Check this requirement"}</button>
    </>}
    {busy && <p role="status">Retrieving and checking original story evidence…</p>}
    {state?.result && <>
      <h4>{labels[state.result.judgment.support]}</h4>
      <p>{state.result.judgment.explanation}</p>
      {state.result.judgment.limitation && <p><strong>Limit:</strong> {state.result.judgment.limitation}</p>}
      {state.result.judgment.clarification && <p><strong>Question for you:</strong> {state.result.judgment.clarification}</p>}
      <small>Searched {state.result.searched_count} of {state.result.source_count} story documents. No resume or coaching search. Missing evidence does not mean missing skill.</small>
      {state.result.passages.map(p => <section key={p.passage_id + p.quote}>
        <h4>{p.source_title} · {p.employer}</h4><blockquote>{p.quote}</blockquote>
        <small>{p.source_class} · {p.source_path} · lines {p.start_line}–{p.end_line}</small>
        {p.source_caveats.length > 0 && <details><summary>Source qualifications</summary>{p.source_caveats.map((c, i) => <p key={i}>{c}</p>)}</details>}
        <details><summary>Read original passage</summary><p className="description">{p.text}</p></details>
      </section>)}
      {state.result.judgment.fact_ids.length > 0 && <p>Referenced registry facts: {state.result.judgment.fact_ids.join(", ")}</p>}
      <p className="muted">AI interpretation, not an approved fit assessment. Quotes and IDs were checked; relevance still needs your review. Reopen this panel after source changes.</p>
    </>}
  </section>;
}
