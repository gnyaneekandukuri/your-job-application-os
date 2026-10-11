import {TeamMessage,isYCListing} from './TeamMessage';
import { TailorResume } from "./TailorResume";
import { RecommendationWhy } from './RecommendationWhy';
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { analysisFilter, processingStatus, recentResultsFirst, type Result } from "./assessmentQueue";
type State={ready:boolean;items:Result[];queued_count:number;settings:{auto_assess:boolean;auto_assess_pause_reason?:string}};
const filters=[["all","All"],["worth","Worth applying"],["review","Needs review"],["consider_passing","Consider passing"],["unavailable","Unavailable"]];
const recommendation=(r:Result)=>r.assessment.listing_changed?'Review updated JD':r.assessment.state!=='COMPLETE'?'Not finished':!r.assessment.recommendation?'Recommendation unavailable':r.assessment.recommendation==='REVIEW'?'Needs review before deciding':r.assessment.recommendation==='SKIP'?'Consider passing':r.assessment.recommendation==='ATTACK_HARD'?'Worth applying · prioritize':'Worth applying';
export function AssessmentResults({onOpen,onDiscover,onUpdatedJD,initialTab="all"}:{initialTab?:string;onUpdatedJD:(id:string)=>void;onDiscover:()=>void;onOpen:(id:string)=>Promise<void>}) {
 const [data,setData]=useState<State|null>(null),[filter,setFilter]=useState(filters.some(([id])=>id===initialTab)?initialTab:'all'),[error,setError]=useState(''),[saving,setSaving]=useState<string|null>(null),[notice,setNotice]=useState('');
 const status=useQuery({queryKey:['job-list-status'],queryFn:async({signal})=>{const r=await fetch('/api/discovery/status',{signal});if(!r.ok)throw new Error('Could not check saved results.');return r.json() as Promise<{ready:boolean;revision:string;refreshing:boolean;error:string|null}>;},refetchInterval:4000,refetchIntervalInBackground:true});
 useEffect(()=>{if(!status.data?.ready)return;let live=true;const controller=new AbortController();
 const load=async()=>{try{const r=await fetch('/api/assessments/saved',{signal:controller.signal});if(!r.ok)throw new Error('Could not load analyzed jobs.');const value=await r.json();if(live){if(value.ready)setData(value);setError('');}}catch(e){if(live)setError(String(e));}};
 void load();return()=>{live=false;controller.abort();};},[status.data?.revision]);
 const applications=useQuery({queryKey:['applications'],queryFn:async()=>{const r=await fetch('/api/applications');if(!r.ok)throw Error('Could not load application status.');return r.json() as Promise<{job_id:string;url:string}[]>;}});
 const all=(data?.items??[]).filter(r=>!applications.data?.some(a=>a.job_id===r.job_id||(a.url&&a.url.replace(/\/$/,'')===r.url?.replace(/\/$/,'')))),items=all.filter(r=>analysisFilter(r,filter)).sort(recentResultsFirst);
 const active=all.filter(r=>r.assessment.state==='RUNNING');
 const processing=all.filter(r=>processingStatus(r)&&r.assessment.state!=='RUNNING');
 const pending=(data?.queued_count??0)+active.length;
 const open=async(id:string)=>{try{await onOpen(id);}catch(e){setError(String(e));}};
 async function choose(r:Result,choice:'SKIP'|'CASUAL') {
  setSaving(r.job_id);setError('');setNotice('');
  try {
   const current=await fetch(`/api/jobs/${r.job_id}/pursuit`);if(!current.ok)throw new Error('Could not load the current recommendation.');const view=await current.json();
   if(!view.version||!view.result)throw new Error('Review the current assessment before deciding.');
   const response=await fetch(`/api/jobs/${r.job_id}/pursuit/decision`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({version:view.version,choice,reason:''})});
   const saved=await response.json();if(!response.ok)throw new Error(saved.detail||'Could not save your choice.');
   setData(d=>d?{...d,items:d.items.map(i=>i.job_id===r.job_id?{...i,assessment:{...i.assessment,choice:saved.choice.choice}}:i)}:d);
   setNotice(`${r.company}: ${choice==='SKIP'?'marked as passed.':'saved as a role you want to pursue.'}`);
  }catch(e){setError(String(e));}finally{setSaving(null);}
 }
 return <section className="panel discovery analyzed-jobs">
 {data&&pending>0&&<section className="discovery-preferences" aria-label="Analysis progress" role="status">
  <strong>{active.length} analyzing now · {data.queued_count} queued</strong>
  {active.map(r=><p key={r.job_id}><strong>{r.company} · {r.title}</strong><br/>{r.assessment.stage?.replace('Matching evidence:', 'Requirements checked for this job:')||'Preparing analysis…'} <button className="text-action" onClick={()=>void open(r.job_id)}>View progress</button></p>)}
  <p>Analysis continues on the local server when you refresh or leave this page. The counter starts at zero for each new job; completed results appear below.</p>
 </section>}
 {data&&processing.length>0&&<details className="discovery-preferences" open={initialTab==='issues'}>
  <summary>Processing status · {processing.length} jobs need attention</summary>
  <p>Unfinished work, outdated results and processing errors. These are separate from application recommendations.</p>
  <div className="discovery-table-scroll"><table className="discovery-table" aria-label="Processing status"><thead><tr><th>Company / role</th><th>Status</th><th>Details</th><th>Action</th></tr></thead><tbody>
   {processing.map(r=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div></td><td>{processingStatus(r)}</td><td>{r.assessment.recommendation_error||r.assessment.error||(r.assessment.state==='STALE'||r.assessment.listing_changed?'Saved results need updating because inputs changed.':r.assessment.stage)}</td><td>{r.assessment.listing_changed&&r.opportunity_id?<button onClick={()=>onUpdatedJD(r.opportunity_id!)}>Review updated JD</button>:<button onClick={()=>void open(r.job_id)}>Open assessment</button>}</td></tr>)}
  </tbody></table></div>
 </details>}
 {data&&<p role="status"><strong>{all.filter(r=>r.assessment.state==='COMPLETE').length} completed analyses</strong> · Scores stay visible even when a recommendation needs review or could not finish.</p>}
 <div className="analysis-toolbar"><label>Recommendation<select value={filter} onChange={e=>setFilter(e.target.value)}>{filters.map(([id,label])=><option key={id} value={id}>{label}{data?` (${all.filter(r=>analysisFilter(r,id)).length})`:''}</option>)}</select></label><span>Deep assessment explains the fit. You decide whether to pursue the job.</span></div>
 <p className="discovery-clock">Latest scan first · highest score within each scan.</p>
 {data?.settings.auto_assess_pause_reason&&<p role="status">{data.settings.auto_assess_pause_reason}</p>}
 {status.data?.refreshing&&data&&<p role="status">Updating saved results…</p>}
 {(status.error||status.data?.error)&&<p role="alert">{status.error?.message||status.data?.error}</p>}
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {!data?<p>Loading analyzed jobs…</p>:!items.length?<div className="decision-empty"><h2>{pending>0?"No completed jobs match this filter yet":"No jobs in this view"}</h2><p>{pending>0?"Analysis is underway above. Choose All recommendations to see every completed result.":"No completed analyses have this recommendation. Choose All to see every completed result."}</p><button onClick={()=>setFilter("all")}>Show all recommendations</button><button onClick={onDiscover}>Go to New jobs</button></div>:<div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Analyzed jobs"><table className="discovery-table"><thead><tr><th>Company / role</th><th>Evidence score</th><th>Listing</th><th>Recommendation & why</th><th>Next action</th></tr></thead><tbody>
 {items.map(r=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div>{r.last_scanned_at&&<small>Seen in scan #{r.last_scan_id} · {new Date(r.last_scanned_at).toLocaleString()}</small>}{r.assessment.assessed_at&&<small>Analyzed · {new Date(r.assessment.assessed_at).toLocaleString()}</small>}</td><td>{r.assessment.score===null?'—':`${r.assessment.score}/100`}{r.assessment.provisional&&<small>Provisional</small>}{r.assessment.state==='STALE'&&<small>Outdated</small>}</td><td>{r.url&&<a href={r.url} target="_blank" rel="noreferrer">View listing ↗</a>}</td><td><strong>{recommendation(r)}</strong>{r.assessment.explanation&&<RecommendationWhy value={r.assessment.explanation}/>}{!r.assessment.recommendation&&(r.assessment.recommendation_error||r.assessment.error)&&<p>{r.assessment.recommendation_error||r.assessment.error}</p>}{r.assessment.choice&&<small>Your choice: {r.assessment.choice==='SKIP'?'Passed':'Want to pursue'}</small>}</td><td>
 {r.assessment.listing_changed&&r.opportunity_id?<button onClick={()=>onUpdatedJD(r.opportunity_id!)}>Review updated JD</button>:<button onClick={()=>void open(r.job_id)}>{r.assessment.state==='COMPLETE'?'Review evidence':'Open unfinished analysis'}</button>}
 {r.assessment.state==='COMPLETE'&&!r.assessment.listing_changed&&<><TailorResume jobId={r.job_id} label={`${r.company} · ${r.title}`}/>{(r.yc_listing||isYCListing(r.url))&&<TeamMessage jobId={r.job_id} company={r.company} url={r.url}/>}<button disabled={!!saving||!!status.data?.refreshing} onClick={()=>void choose(r,r.assessment.choice==='SKIP'?'CASUAL':'SKIP')}>{saving===r.job_id?'Saving…':r.assessment.choice==='SKIP'?'Reconsider':'Pass'}</button></>}
 </td></tr>)}
 </tbody></table></div>}
 </section>;
}
