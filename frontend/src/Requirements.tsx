import { useEffect, useRef, useState } from "react";
import { FeedbackLearning } from "./FeedbackLearning";
import { PursuitAssessment } from "./PursuitAssessment";
import { ReviewConversation } from "./ReviewConversation";
type Requirement = { id: string; interpretation: string; quote: string; category: string };
type Assessment = { alignment: "strong" | "partial" | "insufficient_evidence"; relationship: "similar_work" | "transferable" | "not_established"; confidence: "high" | "medium" | "low"; confidence_reason: string; ownership: string; scope: string; outcomes: string; transfer_reason: string; recency_reason: string };
type Match = { judgment: { assessment?: Assessment | null; support: string; explanation: string; limitation: string; clarification: string }; passages: { source_title: string; quote: string; source_path: string; start_line: number; text: string }[] };
type Dimension = {id:string;label:string;percent:number|null;weight_percent:number;contribution:number;items:{requirement_id:string;anchor:string;reason:string;earned:number;maximum:number}[]};
type Score = { method: string; dimensions: Dimension[]; coverage_percent?:number; assessed?:number; total?:number; version: string; percent: number | null; earned: number; maximum: number; pending: number; provisional: boolean; excluded_ambiguous: number; required_attention: string[]; items: {requirement_id: string; weight: number; earned: number}[] };
type Update = { state: string; message: string; source_title: string };
type State = { workflow?: {status:string;input_version:string;next:string[]}; experience_update: Update | null; previous_run: State["run"]; previous_score: Score | null; score: Score | null; input_version: string; ready: boolean; unready_sources: string[]; previous_result_outdated: boolean; usage: { cap_usd: number | null }; run: null | { state: string; stage: string; error: string; requirements: Requirement[]; results: Record<string, Match>; warnings: { explanation: string; quote: string }[]; empty_reason: string } };
export function Requirements({ jobId }: { jobId: string }) {
  const [state, setState] = useState<State | null>(null);
  const [learningNotice,setLearningNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [reviewFocus,setReviewFocus] = useState<{id:string;nonce:number}|null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await fetch(`/api/jobs/${jobId}/analysis`);
        if (!response.ok) throw new Error("Could not load analysis. Your saved work is retained.");
        const data = await response.json();
        if (!cancelled) { setState(data); setError(""); }
      } catch (e) { if (!cancelled) setError(String(e)); }
      finally { if (!cancelled) timer = setTimeout(poll, 3000); }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [jobId]);
  async function start() {
    if (!state || lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const response = await fetch(`/api/jobs/${jobId}/analysis`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Local-Review": "1" },
        body: JSON.stringify({ consent_to_send: true, input_version: state.input_version }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Analysis could not start.");
      setState(data);
    } catch (e) { setError(String(e)); }
    finally { lock.current = false; setBusy(false); }
  }
  async function acceptAnalysis() {
    if (!state || lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const response = await fetch(`/api/jobs/${jobId}/analysis/review`, {
        method: "POST", headers: {"Content-Type":"application/json", "X-Local-Review":"1"},
        body: JSON.stringify({input_version:state.input_version, action:"accept"}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save your review.");
      setState(data);
    } catch(e) { setError(String(e)); }
    finally { lock.current = false; setBusy(false); }
  }
  async function refresh() {
    const response = await fetch(`/api/jobs/${jobId}/analysis`);
    if (response.ok) setState(await response.json());
  }
  async function retryUpdate() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const response = await fetch(`/api/jobs/${jobId}/experience/retry`, {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Local-Review': '1'}, body: JSON.stringify({consent_to_send: true})});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Update failed.');
      await refresh();
    } catch(e) { setError(String(e)); }
    finally { lock.current = false; setBusy(false); }
  }
  const run = state?.run ?? state?.previous_run;
  const outdated = !state?.run && !!state?.previous_run;
  const score = outdated ? state?.previous_score : state?.score;
  const updating = state?.experience_update?.state === 'UPDATING';
  const running = run?.state === "RUNNING";
  const done = Object.keys(run?.results ?? {}).length;
  const reviewReady = run?.state === "COMPLETE" && done > 0 && !outdated && !state?.previous_result_outdated;
  const labels: Record<string, string> = { unclear: "Needs clarification", not_documented: "Not documented", transferable: "Transferable", direct: "Direct evidence" };
  const counts = Object.fromEntries(Object.keys(labels).map(key => [key, Object.values(run?.results ?? {}).filter(r => r.judgment.support === key).length]));
  const alignments = {strong: "Strong alignment", partial: "Partial alignment", insufficient_evidence: "Not enough evidence"};
  const relationships = {similar_work: "Similar work", transferable: "Transferable experience", not_established: "Relationship not established"};
  return <section className="assessment" aria-label="Whole-role evidence report">
    <div className="eyebrow">REQUIREMENTS + YOUR EVIDENCE</div>
    <h3>{run?.state === "COMPLETE" ? "Your evidence report" : "Analyze this role"}</h3>
    <p>Your evidence score and pursuit recommendation are prepared in one analysis. Review or correct either here.</p>
    <details><summary>The same experience rubric for every role</summary><p>Alignment: strong, partial, or not enough evidence. Transferability is separate: experience in a different context can still be strongly aligned.</p><p>Evidence confidence describes how clearly the sources establish your actions, ownership and scope. It is not a hiring probability. Each row explains outcomes, gaps and how dates affected evidence selection.</p><p>The documented fit score summarizes alignment. Review the cited reasoning and gaps alongside it; evidence confidence is shown separately and does not multiply the score.</p></details>
    {!state && !error && <p role="status">Loading analysisâ€¦</p>}
    {learningNotice && <p role="status">{learningNotice}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {state?.experience_update && <section aria-label="Experience update" role="status"><strong>{state.experience_update.source_title}</strong><p>{state.experience_update.message}</p>
      {state.experience_update.state === 'PENDING' && <><p>Retry sends the saved experience for paid preparation and reassessment with usage tracked. It does not save a duplicate.</p><button disabled={busy || running} onClick={() => void retryUpdate()}>Retry saved experience update</button></>}
    </section>}
    {state?.previous_result_outdated && <p className="error">Sources or analysis logic changed. The previous score and evidence below are outdated. Update the analysis for a current report.</p>}
    {state && !state.ready && <p className="error">Prepare current stories first: {state.unready_sources.join(", ") || "No stories available"}.</p>}
    {state && !running && !updating && state.experience_update?.state !== 'PENDING' && (outdated || run?.state !== "COMPLETE") && <>
      <p className="muted">This starts paid analysis using your JD, story passages, fact/conflict rules and pursuit preferences with OpenAI, with usage tracked and no local spending cap. Completed work is reused. The pursuit recommendation follows automatically.</p>
      <button disabled={busy || !state.ready} onClick={() => void start()}>{busy ? "Startingâ€¦" : run ? "Resume unfinished analysis" : "Analyze this role"}</button>
    </>}
    {run && <>
      <p role="status">{run.stage}</p>
      {running && <progress aria-label="Requirements matched" max={run.requirements.length || 1} value={done} />}
      <p>{done} of {run.requirements.length} requirements analyzed{running && !run.requirements.length ? " Â· extracting requirementsâ€¦" : ""}.</p>
      {score && done > 0 && <div className="role-score-summary" aria-label="Overall fit summary"><strong className="score-value">{score.percent===null?'Not scored':`${score.percent}/100`}</strong><span>{outdated?'Outdated evidence score':score.provisional?'Provisional evidence score':'Documented fit'} Â· {done}/{run.requirements.length} checked</span></div>}
      {reviewReady && <section className="review-checkpoint" aria-label="Review your analysis">
        <h3>{state?.workflow?.status === "REVIEWED" ? "Analysis kept as shown" : "Does this analysis reflect your experience?"}</h3>
        <p>{state?.workflow?.status === "REVIEWED" ? "You can revisit the evidence whenever you need to." : "Keep it as shown, or add missing context and question an assessment."}</p>
        {state?.workflow?.status !== "REVIEWED" && <button disabled={busy} onClick={()=>void acceptAnalysis()}>{busy ? "Saving reviewâ€¦" : "Looks right"}</button>}
        <button onClick={()=>setReviewFocus({id:"",nonce:Date.now()})}>Review or add context</button>
      </section>}
      {reviewReady && <PursuitAssessment key={state!.input_version} jobId={jobId} onReviewEvidence={()=>setReviewFocus({id:"",nonce:Date.now()})} onChanged={()=>{setLearningNotice("Feedback changes saved with history. Update any assessments marked outdated before making the next decision.");void refresh();}}/> }
      {score && done > 0 && <details className="score-breakdown"><summary>Dimension scores and scoring details</summary>
      {score && done > 0 && <section className="match-score" aria-label="Documented job fit score">
        <div className="eyebrow">DOCUMENTED JOB FIT {outdated ? "Â· OUTDATED â€” BEFORE YOUR UPDATE" : score.provisional ? "Â· PROVISIONAL" : "Â· ANALYSIS COMPLETE"}</div>
        <strong className="score-value">{score.percent === null ? "Not scored" : `${score.percent}/100`}</strong>
        {score.method === 'dimensions' ? <>
          <p>Overall documented fit from five dimensions. {score.assessed}/{score.total} requirements assessed ({score.coverage_percent}% weighted coverage).</p>
          <div className="dimension-grid">{score.dimensions.map(d=><details key={d.id} className="dimension-card"><summary><span>{d.label}</span><strong>{d.percent===null?'Not applicable':`${d.percent}/100`}</strong><small>{d.weight_percent.toFixed(0)}% weight Â· {d.contribution.toFixed(2)} total points</small></summary>
            <p>{d.weight_percent.toFixed(0)}% of total Â· {d.contribution.toFixed(2)} points contributed.</p>
            <progress max={100} value={d.percent??0} aria-label={d.label}/>
            {d.items.map(i=><div key={i.requirement_id} className="dimension-item"><strong>{run.requirements.find(r=>r.id===i.requirement_id)?.interpretation}</strong><p>{i.anchor.replaceAll('_',' ')} Â· {i.earned}/{i.maximum} weighted points</p><p>{i.reason}</p>{reviewReady && <button onClick={()=>setReviewFocus({id:i.requirement_id,nonce:Date.now()})}>Clarify this requirement</button>}</div>)}
          </details>)}</div>
        </> : <p>Previous alignment-only score: {score.earned} of {score.maximum} weighted points. Reanalyze for the five-dimension breakdown.</p>}
        {score.provisional && <p>Provisional: {score.pending} checks pending. {score.method==='dimensions'?'Pending checks are excluded from dimension averages and reported separately as coverage.':'This older score retained pending checks in its denominator.'}</p>}
        {score.required_attention.length > 0 && <div className="score-attention"><strong>{score.required_attention.length} explicit qualifications are not fully supported:</strong><ul>{run.requirements.filter(r => score!.required_attention.includes(r.id)).map(r => <li key={r.id}>{r.interpretation}</li>)}</ul><p>A high overall score does not override these items.</p></div>}
        <details><summary>How this score is calculated</summary>
          <p>Each applicable dimension is anchored: demonstrated = 100%, partial = 50%, insufficient evidence = 0%. Requirements and responsibilities weigh 2; preferences weigh 1. Ambiguous wording is excluded. Not-applicable dimensions are excluded with an explanation, never used to hide missing evidence.</p>
          <p>Each dimension averages its assessed applicable requirements. Overall fit averages the five dimension scores at 20% each; if a dimension is entirely inapplicable, the remaining weights are normalized. Expand a dimension to see each contribution. Gaps do not subtract points again.</p>
          <p>Confidence and assessment coverage stay separate. These are transparent product defaults, not validated hiring probabilities. Mandatory credentials and eligibility remain explicit checks outside capability dimensions.</p>
          <small>{score.version}</small>
        </details>
      </section>}
      </details>}
      {done > 0 && <p className="coverage-summary"><strong>Evidence at a glance{run.state !== "COMPLETE" ? " (partial)" : ""}:</strong> {counts.direct} strong alignment; {counts.transferable} partial alignment; {counts.unclear + counts.not_documented} insufficient evidence. Transferable experience can be a strong match. Confidence describes documentation quality, not hiring probability.</p>}
      {run.error && <p className="error" role="alert">{run.error}</p>}
      {run.empty_reason && <p>{run.empty_reason}</p>}
      {run.warnings.map((w, i) => <p className="error" key={i}>{w.explanation} â€” {w.quote}</p>)}
      {run.requirements.length > 0 && <div className="evidence-table-scroll" tabIndex={0} role="region" aria-label="Requirement comparison table">
        <table className="evidence-table">
          <caption>How your experience maps to this role â€” every requirement in one view</caption>
          <thead><tr><th scope="col">Job requirement</th><th scope="col">Alignment & confidence</th><th scope="col">Relevant experience & why it fits</th><th scope="col">Transferability, gaps & questions</th></tr></thead>
          <tbody>{run.requirements.map(item => {
            const match = run.results[item.id];
            const assessment = match?.judgment.assessment;
            const points = score?.items.find(r => r.requirement_id === item.id);
            return <tr key={item.id}>
              <th scope="row">{item.interpretation}<small className="requirement-kind">{item.category === "preference" ? "Preferred, not required" : item.category === "unclear" ? "JD wording ambiguous" : item.category === "responsibility" ? "Responsibility" : "Explicit requirement"}</small><details><summary>JD quote</summary><blockquote>{item.quote}</blockquote><small>{item.id}</small></details></th>
              <td><span className={`evidence-strength ${match?.judgment.support ?? "pending"}`}>{assessment ? alignments[assessment.alignment] : match ? "Legacy assessment" : "Pending"}</span>
                {assessment && <><p className="requirement-kind">{relationships[assessment.relationship]}</p><strong>Evidence confidence: {assessment.confidence}</strong><p>{assessment.confidence_reason}</p></>}
                {points && <small className="requirement-kind">{score?.method === "dimensions" ? "Point contributions are shown in the dimension breakdown." : points.weight ? `${points.earned}/${points.weight} alignment points` : "Not scored"}</small>}</td>
              <td>{match ? <><p>{match.judgment.explanation}</p>
                {assessment && <details><summary>Ownership, scope & outcomes</summary><p><strong>Ownership:</strong> {assessment.ownership}</p><p><strong>Scope:</strong> {assessment.scope}</p><p><strong>Outcomes:</strong> {assessment.outcomes}</p><p><strong>Recency:</strong> {assessment.recency_reason}</p></details>}
                {match.passages.length > 0 && <details><summary>Evidence Â· {[...new Set(match.passages.map(p => p.source_title))].join(", ")}</summary>
                  {match.passages.map((p, i) => <div key={i}><strong>{p.source_title}</strong><blockquote>{p.quote}</blockquote><small>{p.source_path} Â· line {p.start_line}</small><details><summary>Original passage</summary><p className="description">{p.text}</p></details></div>)}
                </details>}</> : "Not analyzed yet."}</td>
              <td>{match ? <>{assessment && <p>{assessment.transfer_reason}</p>}{match.judgment.limitation ? <p>{match.judgment.limitation}</p> : <p>No limitation stated by the model; not independently verified.</p>}
                {match.judgment.clarification && <p><strong>Question:</strong> {match.judgment.clarification}</p>}
                {assessment && reviewReady && <button onClick={()=>setReviewFocus({id:item.id,nonce:Date.now()})}>Clarify this requirement</button>}</> : "Pending"}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
      {done > 0 && <ReviewConversation jobId={jobId} inputVersion={state!.input_version} requirements={run.requirements} focus={reviewFocus} onOpen={()=>setReviewFocus({id:"",nonce:Date.now()})} onClose={()=>setReviewFocus(null)} onChanged={()=>void refresh()}/>}
      <p className="muted">Missing documentation is not proof of a missing skill. Source quotes are checked; AI interpretation still needs your review. No resume changes or applications.</p>
    </>}
    {(!reviewReady) && <FeedbackLearning jobId={jobId} version={null} choice={null} onReviewEvidence={()=>setReviewFocus({id:"",nonce:Date.now()})} onChanged={()=>{setLearningNotice("Feedback changes saved with history. Update assessments marked outdated.");void refresh();}}/>}
  </section>;
}
