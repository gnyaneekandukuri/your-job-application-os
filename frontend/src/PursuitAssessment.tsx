import { useEffect, useState } from 'react';
import { FeedbackLearning } from './FeedbackLearning';

type Choice = 'SKIP'|'CASUAL'|'ATTACK_HARD';
const labels:Record<Choice,string>={SKIP:'Skip',CASUAL:'Apply normally',ATTACK_HARD:'Attack Hard'};
type Signal={kind:string;finding:string;quote:string;reason:string};
type Result={recommendation:Choice;rationale:string;score:number;created_at:string;
  input_versions:Record<string,string>;source_url:string|null;
  constraints:Signal[];priorities:Signal[];limitations:string[];what_would_change_it:string[];
  checks:{label:string;passed:boolean;detail:string}[];
  core_evidence:{requirement_id:string;requirement:string;alignment:string;reason:string;fact_ids:string[];
    citations:{source_title:string;source_path:string;start_line:number;quote:string}[]}[];};
type View={automatic?:{state:string;error?:string};ready:boolean;reason:string;version:string|null;result:Result|null;choice:{choice:Choice;reason:string;decided_at:string}|null};
export function PursuitAssessment({jobId,onChanged=()=>{},onReviewEvidence=()=>{}}:{jobId:string;onChanged?:()=>void;onReviewEvidence?:()=>void}) {
  const [view,setView]=useState<View|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [choice,setChoice]=useState<Choice>('CASUAL');
  const [reason,setReason]=useState('');
  function receive(data:View){setView(data);setChoice(data.choice?.choice??data.result?.recommendation??'CASUAL');setReason(data.choice?.reason??'');}
  useEffect(()=>{
    let cancelled=false;
    async function load(){
      try {
        let response=await fetch(`/api/jobs/${jobId}/pursuit`);
        if(!response.ok)throw new Error('Could not load pursuit assessment.');
        let data:View=await response.json();
        if(!cancelled)receive(data);
        if(data.ready&&!data.result){
          if(!cancelled)setBusy(true);
          response=await fetch(`/api/jobs/${jobId}/pursuit/automatic`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({version:data.version,consent_to_send:true})});
          data=await response.json();
          if(!response.ok)throw new Error((data as unknown as {detail:string}).detail||'Could not finish pursuit assessment.');
          if(!cancelled)receive(data);
        }
      }catch(e){if(!cancelled)setError(String(e));}
      finally{if(!cancelled)setBusy(false);}
    }
    void load();return()=>{cancelled=true;};
  },[jobId,view?.version]);
  async function act(decide=false){
    if(busy||!view?.version)return;setBusy(true);setError('');
    try{
      const r=await fetch(`/api/jobs/${jobId}/pursuit${decide?'/decision':''}`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},
        body:JSON.stringify(decide?{version:view.version,choice,reason}:{version:view.version,consent_to_send:true})});
      const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Pursuit assessment failed.');receive(data);
    }catch(e){setError(String(e));const r=await fetch(`/api/jobs/${jobId}/pursuit`).catch(()=>null);if(r?.ok)receive(await r.json());}
    finally{setBusy(false);}
  }
  const result=view?.result;
  return <section className="pursuit-assessment" aria-label="Pursuit recommendation">
    <div className="eyebrow">CHOOSE YOUR NEXT STEP</div>
    <h3>Pursuit recommendation</h3>
    {!view&&!error&&<p role="status">Loading…</p>}
    {view&&!view.ready&&<p>{view.reason}</p>}
    {view?.ready&&!result&&<>
      <p role="status">{busy||!view.automatic?'Finishing your pursuit recommendation…':view.automatic.error||'The automatic assessment was interrupted. Your evidence score is saved.'}</p>
      {!busy&&view.automatic&&<button onClick={()=>void act()}>Retry recommendation</button>}
    </>}
    {result&&<>
      <h4>Recommendation: {labels[result.recommendation]}</h4><p>{result.rationale}</p>
      <p>Documented fit: <strong>{result.score}/100</strong>. Personal preferences do not change this score.</p>
      <details><summary>Why this recommendation?</summary>
        <h4>Criteria for extra effort</h4><ul>{result.checks.map(c=><li key={c.label}><strong>{c.passed?'Meets':'Not yet met'} — {c.label}:</strong> {c.detail}</li>)}</ul>
        <h4>Constraints and uncertainties</h4>{result.constraints.map(s=><article key={s.kind}><strong>{s.kind.replaceAll('_',' ')} · {s.finding}</strong><p>{s.reason}</p>{s.quote&&<blockquote>{s.quote}</blockquote>}</article>)}
        <h4>Personal priorities</h4>{result.priorities.map(s=><article key={s.kind}><strong>{s.kind.replaceAll('_',' ')} · {s.finding.replaceAll('_',' ')}</strong><p>{s.reason}</p>{s.quote&&<blockquote>{s.quote}</blockquote>}</article>)}
        {result.source_url&&<a href={result.source_url} target="_blank" rel="noreferrer">Original job posting</a>}
        <p>Quotes above are from the saved posting, not a fresh company check.</p>
      </details>
      <details><summary>Core work and supporting evidence</summary>{result.core_evidence.map(e=><article key={e.requirement_id}><strong>{e.requirement}</strong><p>{e.alignment.replaceAll('_',' ')} — {e.reason}</p>
        {e.citations.map((c,i)=><div key={i}><blockquote>{c.quote}</blockquote><small>{c.source_title} · {c.source_path} · line {c.start_line}</small></div>)}
        {e.fact_ids.length>0&&<small>Fact references: {e.fact_ids.join(', ')}</small>}</article>)}</details>
      {result.what_would_change_it.length>0&&<details><summary>What could change the recommendation?</summary><ul>{result.what_would_change_it.map((v,i)=><li key={i}>{v}</li>)}</ul><p>You can revisit the evidence through Review or add context above.</p></details>}
      <details><summary>Limits and assessment versions</summary><ul>{result.limitations.map(v=><li key={v}>{v}</li>)}</ul><p>Initial product defaults; not validated hiring predictions.</p>{Object.entries(result.input_versions).map(([k,v])=><p key={k}><strong>{k}:</strong> <small>{v}</small></p>)}</details>
      <details><summary>Choose or change my pursuit decision</summary><fieldset disabled={busy}><legend>Your decision</legend>
        <label>Pursuit level<select value={choice} onChange={e=>setChoice(e.target.value as Choice)}>{Object.entries(labels).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
        <label>{choice!==result.recommendation?'Why do you prefer this pursuit level?':'Reason or feedback (optional)'}<textarea rows={2} maxLength={2000} value={reason} onChange={e=>setReason(e.target.value)}/></label>
        <button onClick={()=>void act(true)}>{busy?'Saving…':view?.choice?'Update my decision':'Save my decision'}</button>
      </fieldset></details>
      {view?.choice&&<p role="status">Your saved decision: <strong>{labels[view.choice.choice]}</strong>. This records your plan; it does not edit your resume or submit an application.</p>}
    </>}
    <details><summary>Feedback and criteria improvements</summary><FeedbackLearning jobId={jobId} version={view?.version??null} choice={view?.choice??null} onReviewEvidence={onReviewEvidence} onChanged={()=>{onChanged();void fetch(`/api/jobs/${jobId}/pursuit`).then(r=>r.json()).then(receive).catch(e=>setError(String(e)));}}/></details>
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
