import { useEffect, useState } from "react";

type Result = {job_id:string;company:string;title:string;url:string;assessment:{state:string;score:number|null;provisional?:boolean;recommendation:string|null;stage?:string;error?:string;recommendation_error?:string}};
type State = {items:Result[];queued_count:number;settings:{auto_assess:boolean;auto_assess_pause_reason?:string}};
const group = (r:Result) => r.assessment.state === "COMPLETE" && r.assessment.recommendation ? "complete" : r.assessment.state === "RUNNING" ? "running" : "attention";

export function AssessmentResults({onOpen}:{onOpen:(id:string)=>Promise<void>}) {
  const [data,setData]=useState<State|null>(null);
  const [tab,setTab]=useState("complete");
  const [error,setError]=useState("");
  useEffect(()=>{
    let live=true;let timer:ReturnType<typeof setTimeout>;
    const load=async()=>{try {const r=await fetch('/api/assessments');if(!r.ok)throw new Error('Could not load analysis results.');const value=await r.json();if(live){setData(value);setError('');}} catch(e){if(live)setError(String(e));} finally{if(live)timer=setTimeout(()=>void load(),6000);}};
    void load();return()=>{live=false;clearTimeout(timer);};
  },[]);
  const items=(data?.items??[]).filter(r=>group(r)===tab);
  const limited=data?.items.some(r=>`${r.assessment.error} ${r.assessment.recommendation_error}`.includes('(HTTP 429)'));
  return <section className="panel discovery">
    <div className="section-heading"><div><div className="eyebrow">YOUR ASSESSMENT RESULTS</div><h1>Deep analysis</h1><p>Completed evidence scores and pursuit recommendations.</p></div></div>
    {error && <p role="alert">{error}</p>}
    {data && !data.settings.auto_assess && limited && <p role="status">Automatic analysis is paused after OpenAI returned a quota or rate-limit error. Completed results are saved. Check your API limits before retrying failed roles.</p>}
    <div className="opportunity-filters" aria-label="Analysis status">
      {[["complete","Completed"],["running","In progress"],["attention","Needs attention"]].map(([id,label])=><button key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{label} ({data?.items.filter(r=>group(r)===id).length??0})</button>)}
    </div>
    {!!data?.queued_count && <p>{data.queued_count} roles queued for automatic analysis.</p>}
    {!data ? <p>Loading results…</p> : !items.length ? <p>{tab==="complete"?"No completed score and recommendation yet.":tab==="running"?"No analysis currently running.":"Nothing needs attention."}</p> : <div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Deep analysis results"><table className="discovery-table"><thead><tr><th>Company / role</th><th>Score</th><th>Link</th><th>Pursuit recommendation</th><th>Review</th></tr></thead><tbody>
      {items.map(r=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div></td><td>{r.assessment.score===null?'—':`${r.assessment.score}/100`}{r.assessment.provisional&&r.assessment.score!==null&&<small>Partial result</small>}</td><td>{r.url&&<a href={r.url} target="_blank" rel="noreferrer">View listing ↗</a>}</td><td>{r.assessment.recommendation?.replaceAll('_',' ')||'Not available yet'}</td><td>
        {tab==='running'&&<p role="status">{r.assessment.stage}</p>}
        {tab==='attention'&&<details><summary>{`${r.assessment.error} ${r.assessment.recommendation_error}`.includes('(HTTP 429)')?'API limit reached':r.assessment.state==='COMPLETE'?'Recommendation needs attention':'Analysis needs attention'}</summary><p>{r.assessment.error||r.assessment.recommendation_error||r.assessment.stage}</p></details>}
        <button onClick={()=>void onOpen(r.job_id).catch(e=>setError(String(e)))}>Open analysis</button>
      </td></tr>)}
    </tbody></table></div>}
  </section>;
}
