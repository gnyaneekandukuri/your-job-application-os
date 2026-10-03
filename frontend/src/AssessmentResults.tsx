import { useEffect, useState } from "react";
import { analysisFilter, type Result } from "./assessmentQueue";
type State={items:Result[];queued_count:number;settings:{auto_assess:boolean;auto_assess_pause_reason?:string}};
const filters=[["worth","Worth applying"],["consider_passing","Consider passing"],["all","All analyzed"],["passed","Passed by me"],["issues","Unfinished / needs review"]];
const recommendation=(r:Result)=>r.assessment.listing_changed?'Review updated JD':r.assessment.state!=='COMPLETE'?'Not finished':r.assessment.recommendation==='SKIP'?'Consider passing':r.assessment.recommendation==='ATTACK_HARD'?'Worth applying · prioritize':'Worth applying';
export function AssessmentResults({onOpen,onDiscover,onUpdatedJD,initialTab="worth"}:{initialTab?:string;onUpdatedJD:(id:string)=>void;onDiscover:()=>void;onOpen:(id:string)=>Promise<void>}) {
 const [data,setData]=useState<State|null>(null),[filter,setFilter]=useState(filters.some(([id])=>id===initialTab)?initialTab:'worth'),[error,setError]=useState(''),[saving,setSaving]=useState<string|null>(null),[notice,setNotice]=useState('');
 useEffect(()=>{let live=true;let timer:ReturnType<typeof setTimeout>;const controller=new AbortController();
 const load=async()=>{try{const r=await fetch('/api/assessments',{signal:controller.signal});if(!r.ok)throw new Error('Could not load analyzed jobs.');const value=await r.json();if(live){setData(value);setError('');}}catch(e){if(live)setError(String(e));}finally{if(live)timer=setTimeout(()=>void load(),document.hidden?60000:15000);}};
 void load();return()=>{live=false;controller.abort();clearTimeout(timer);};},[]);
 const all=data?.items??[],items=all.filter(r=>analysisFilter(r,filter)).sort((a,b)=>(b.assessment.score??-1)-(a.assessment.score??-1));
 const open=async(id:string)=>{try{await onOpen(id);}catch(e){setError(String(e));}};
 async function choose(r:Result,choice:'SKIP'|'CASUAL') {
  setSaving(r.job_id);setError('');setNotice('');
  try {
   const current=await fetch(`/api/jobs/${r.job_id}/pursuit`);if(!current.ok)throw new Error('Could not load the current recommendation.');const view=await current.json();
   if(!view.version||!view.result)throw new Error('Review the current assessment before deciding.');
   const response=await fetch(`/api/jobs/${r.job_id}/pursuit/decision`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({version:view.version,choice,reason:''})});
   const saved=await response.json();if(!response.ok)throw new Error(saved.detail||'Could not save your choice.');
   setData(d=>d?{...d,items:d.items.map(i=>i.job_id===r.job_id?{...i,assessment:{...i.assessment,choice:saved.choice.choice}}:i)}:d);
   setNotice(`${r.company}: ${choice==='SKIP'?'moved to Passed by me.':'saved as a role you want to pursue.'}`);
  }catch(e){setError(String(e));}finally{setSaving(null);}
 }
 return <section className="panel discovery analyzed-jobs">
 <div className="analysis-toolbar"><label>Show<select value={filter} onChange={e=>setFilter(e.target.value)}>{filters.map(([id,label])=><option key={id} value={id}>{label}{data?` (${all.filter(r=>analysisFilter(r,id)).length})`:''}</option>)}</select></label><span>Deep assessment explains the fit. You decide whether to pursue the job.</span></div>
 <p className="discovery-clock">Resume tailoring is not built yet. You can review the evidence, keep a role to pursue, or pass.</p>
 {filter==='issues'&&<p>Missing information, changed JDs and unfinished or outdated assessments need review. They are not rejections.</p>}
 {data?.settings.auto_assess_pause_reason&&<p role="status">{data.settings.auto_assess_pause_reason}</p>}
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {!data?<p>Loading analyzed jobs…</p>:!items.length?<div className="decision-empty"><h2>No jobs in this view</h2><p>Select a different filter, or choose a job in New jobs for deep analysis.</p><button onClick={onDiscover}>Go to New jobs</button></div>:<div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Analyzed jobs"><table className="discovery-table"><thead><tr><th>Company / role</th><th>Evidence score</th><th>Listing</th><th>Recommendation & why</th><th>Next action</th></tr></thead><tbody>
 {items.map(r=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div></td><td>{r.assessment.score===null?'—':`${r.assessment.score}/100`}{r.assessment.provisional&&<small>Provisional</small>}{r.assessment.state==='STALE'&&<small>Outdated</small>}</td><td>{r.url&&<a href={r.url} target="_blank" rel="noreferrer">View listing ↗</a>}</td><td><strong>{recommendation(r)}</strong><p>{r.assessment.rationale||r.assessment.error||r.assessment.recommendation_error||r.assessment.stage}</p>{r.assessment.choice&&<small>Your choice: {r.assessment.choice==='SKIP'?'Passed':'Want to pursue'}</small>}</td><td>
 {r.assessment.listing_changed&&r.opportunity_id?<button onClick={()=>onUpdatedJD(r.opportunity_id!)}>Review updated JD</button>:<button onClick={()=>void open(r.job_id)}>{r.assessment.state==='COMPLETE'?'Review evidence':'Open unfinished analysis'}</button>}
 {r.assessment.state==='COMPLETE'&&r.assessment.recommendation&&!r.assessment.listing_changed&&<><button disabled title="Resume tailoring is not built yet">Tailor resume · coming next</button><button disabled={!!saving} onClick={()=>void choose(r,r.assessment.choice==='SKIP'?'CASUAL':'SKIP')}>{saving===r.job_id?'Saving…':r.assessment.choice==='SKIP'?'Reconsider':'Pass'}</button></>}
 </td></tr>)}
 </tbody></table></div>}
 </section>;
}
