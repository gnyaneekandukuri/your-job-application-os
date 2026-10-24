import {ResumeApproach} from './ResumeApproach';
import {MarkApplied} from './MarkApplied';
import {WritingHelp,isYCListing} from './TeamMessage';
import { TailorResume } from "./TailorResume";
import { RecommendationWhy } from './RecommendationWhy';
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { analysisFilter, helpFor, recentResultsFirst, type Result } from "./assessmentQueue";
type State={ready:boolean;items:Result[];queued_count:number;settings:{auto_assess:boolean;auto_assess_pause_reason?:string}};
const filters=[["all","All"],["worth","Worth applying"],["review","Needs review"],["consider_passing","Consider passing"],["unavailable","Unavailable"],["passed","Passed by me"]];
const recommendation=(r:Result)=>r.assessment.listing_changed?'Review updated JD':r.assessment.state==='STALE'?'Outdated: re-run for a current recommendation':r.assessment.state!=='COMPLETE'?'Not finished':!r.assessment.recommendation?'Recommendation unavailable':r.assessment.recommendation==='REVIEW'?'Needs review before deciding':r.assessment.recommendation==='SKIP'?'Consider passing':r.assessment.recommendation==='ATTACK_HARD'?'Worth applying · prioritize':'Worth applying';
export function AssessmentResults({onOpen,onDiscover,onUpdatedJD,initialTab="all"}:{initialTab?:string;onUpdatedJD:(id:string)=>void;onDiscover:()=>void;onOpen:(id:string)=>Promise<void>}) {
 const [data,setData]=useState<State|null>(null),[filter,setFilter]=useState(filters.some(([id])=>id===initialTab)?initialTab:'all'),[error,setError]=useState(''),[saving,setSaving]=useState<string|null>(null),[notice,setNotice]=useState('');
 const [rowSaving,setRowSaving]=useState<Record<string,boolean>>({});
 const [choices,setChoices]=useState<Record<string,'SKIP'|'CASUAL'>>({});
 const status=useQuery({queryKey:['job-list-status'],queryFn:async({signal})=>{const r=await fetch('/api/discovery/status',{signal});if(!r.ok)throw new Error('Could not check saved results.');return r.json() as Promise<{ready:boolean;revision:string;refreshing:boolean;error:string|null}>;},refetchInterval:4000,refetchIntervalInBackground:true});
 const saved=useQuery({queryKey:['saved-assessments',status.data?.revision],queryFn:async({signal})=>{const r=await fetch('/api/assessments/saved',{signal:AbortSignal.any([signal,AbortSignal.timeout(20000)])});if(!r.ok)throw new Error('Could not load analyzed jobs.');return r.json() as Promise<State>;},placeholderData:previous=>previous});
 useEffect(()=>{if(saved.data?.ready)setData(saved.data);},[saved.data]);
 const applications=useQuery({queryKey:['applications'],queryFn:async()=>{const r=await fetch('/api/applications');if(!r.ok)throw Error('Could not load application status.');return r.json() as Promise<{job_id:string;url:string}[]>;}});
 const all=(data?.items??[]).map(r=>choices[r.job_id]?{...r,assessment:{...r.assessment,choice:choices[r.job_id]}}:r).filter(r=>!applications.data?.some(a=>a.job_id===r.job_id||(a.url&&a.url.replace(/\/$/,'')===r.url?.replace(/\/$/,'')))),items=all.filter(r=>analysisFilter(r,filter)).sort(recentResultsFirst);
 async function checkApproaches(){setSaving('resume-approaches');setError('');try{const r=await fetch('/api/assessments/resume-approaches',{method:'POST',headers:{'X-Local-Review':'1'}});const body=await r.json();if(!r.ok)throw Error(body.detail||'Could not queue resume checks.');setNotice(`${body.queued} resume approach checks queued. Results will appear beside each recommendation.`);}catch(e){setError((e as Error).message);}finally{setSaving(null);}}
 const active=all.filter(r=>r.assessment.state==='RUNNING');
 const queued=all.filter(r=>r.assessment.state==='QUEUED');
 // Paste box for fixing a description without leaving this page.
 const [paste,setPaste]=useState<{r:Result;id:string;title:string;company:string;description:string;expected_hash:string}|null>(null);
 const [fixed,setFixed]=useState<Record<string,boolean>>({});
 const [analyzeNext,setAnalyzeNext]=useState<{company:string;id:string;hash:string}|null>(null);
 const helpRows=all.flatMap(r=>{const h=fixed[r.job_id]?null:helpFor(r);return h?[{r,h}]:[];});
 const [view,setView]=useState<'results'|'help'>(initialTab==='issues'?'help':'results');
 const pending=(data?.queued_count??0)+active.length+queued.length;
 const open=async(id:string)=>{try{await onOpen(id);}catch(e){setError(String(e));}};
 async function passSaved(r:Result,passed:boolean){
  if(rowSaving[r.job_id])return;
  setRowSaving(b=>({...b,[r.job_id]:true}));setError('');setNotice('');
  try{
   const response=await fetch(`/api/assessments/${r.job_id}/pass`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({passed})});
   const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Could not save your choice.');
   setChoices(c=>{const next={...c};if(passed)next[r.job_id]='SKIP';else delete next[r.job_id];return next;});
   setData(d=>d?{...d,items:d.items.map(i=>i.job_id===r.job_id?{...i,assessment:{...i.assessment,choice:passed?'SKIP':null}}:i)}:d);
   setNotice(`${r.company}: ${passed?'passed. Find it under Results → Passed by me.':'restored.'}`);
  }catch(e){setError(String(e));}finally{setRowSaving(b=>({...b,[r.job_id]:false}));}
 }
 const post=(url:string,body:unknown)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body)}).then(async response=>{const v=await response.json();if(!response.ok)throw new Error(typeof v.detail==='string'?v.detail:'Could not complete this action.');return v;});
 async function keepResult(r:Result){
  setError('');setRowSaving(b=>({...b,[r.job_id]:true}));
  try{await post(`/api/assessments/${r.job_id}/keep`,{keep:!r.kept});await saved.refetch();}
  catch(e){setError(String(e));}finally{setRowSaving(b=>({...b,[r.job_id]:false}));}
 }
 async function openPaste(r:Result){
  setError('');setNotice('');
  try{const response=await fetch(`/api/discovery/${r.opportunity_id}/detail`);const d=await response.json();if(!response.ok)throw new Error(typeof d.detail==='string'?d.detail:'Could not open this listing.');
   setPaste({r,id:d.id,title:d.title,company:d.company,description:d.description,expected_hash:d.content_hash});}catch(e){setError(String(e));}
 }
 async function savePaste(){
  if(!paste)return;setSaving(paste.r.job_id);setError('');
  try{const v=await post(`/api/discovery/${paste.id}/description`,{expected_hash:paste.expected_hash,title:paste.title,company:paste.company,description:paste.description});
   setFixed(f=>({...f,[paste.r.job_id]:true}));setPaste(null);
   // You analyzed this job before, so re-analyzing the fixed version is the next step; no second click.
   const q=await post('/api/discovery/assess-selected',{roles:[{id:paste.id,content_hash:v.content_hash}]});
   if(q.accepted.length)setNotice(`${paste.r.company}: description saved and queued for analysis. It appears in Results when it finishes.`);
   else setAnalyzeNext({company:paste.r.company,id:paste.id,hash:v.content_hash});
  }catch(e){setError(String(e));}finally{setSaving(null);}
 }
 async function analyzeUpdated(){
  if(!analyzeNext)return;setError('');
  try{const v=await post('/api/discovery/assess-selected',{roles:[{id:analyzeNext.id,content_hash:analyzeNext.hash}]});
   setNotice(v.accepted.length?`${analyzeNext.company}: queued for analysis. It appears in Results when it finishes.`:`${analyzeNext.company}: ${v.rejected[0]?.reason||'could not be queued.'}`);setAnalyzeNext(null);
  }catch(e){setError(String(e));}
 }
 async function rerun(r:Result){
  setSaving(r.job_id);setError('');setNotice('');
  try{
   const base=`/api/jobs/${r.job_id}/analysis`;const current=await (await fetch(base)).json();
   const response=await fetch(base,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({consent_to_send:true,input_version:current.input_version})});
   const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Could not queue this analysis.');
   setNotice(`${r.company}: queued for analysis. It moves to Results when it finishes.`);void saved.refetch();
  }catch(e){setError(String(e));}finally{setSaving(null);}
 }
 async function choose(r:Result,choice:'SKIP'|'CASUAL') {
  if(choice==='SKIP')return passSaved(r,true);
  if(rowSaving[r.job_id])return;
  setRowSaving(b=>({...b,[r.job_id]:true}));setError('');setNotice('');
  try {
   const current=await fetch(`/api/jobs/${r.job_id}/pursuit`);if(!current.ok)throw new Error('Could not load the current recommendation.');const view=await current.json();
   if(!view.version||!view.result)throw new Error('Review the current assessment before deciding.');
   const response=await fetch(`/api/jobs/${r.job_id}/pursuit/decision`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({version:view.version,choice,reason:''})});
   const saved=await response.json();if(!response.ok)throw new Error(saved.detail||'Could not save your choice.');
   await post(`/api/assessments/${r.job_id}/pass`,{passed:false});
   setChoices(previous=>({...previous,[r.job_id]:saved.choice.choice}));
   setData(d=>d?{...d,items:d.items.map(i=>i.job_id===r.job_id?{...i,assessment:{...i.assessment,choice:saved.choice.choice}}:i)}:d);
   setNotice(`${r.company}: saved as a role you want to pursue.`);
  }catch(e){setError(String(e));}finally{setRowSaving(b=>({...b,[r.job_id]:false}));}
 }
 return <section className="panel discovery analyzed-jobs">
 {data&&pending>0&&<section className="discovery-preferences" aria-label="Analysis progress" role="status">
  <strong>{active.length} analyzing now · {(data.queued_count??0)+queued.length} queued</strong>
  {queued.map(r=><p key={r.job_id}><strong>{r.company} · {r.title}</strong> — Queued for deep analysis</p>)}
  {active.map(r=><p key={r.job_id}><strong>{r.company} · {r.title}</strong><br/>{r.assessment.stage?.replace('Matching evidence:', 'Requirements checked for this job:')||'Preparing analysis…'} <button className="text-action" onClick={()=>void open(r.job_id)}>View progress</button></p>)}
  <p>Analysis continues on the local server when you refresh or leave this page. The counter starts at zero for each new job; completed results appear below.</p>
 </section>}
 <div className="view-tabs" role="tablist" aria-label="Analyzed jobs views"><button role="tab" aria-selected={view==="results"} className={view==="results"?"active":""} onClick={()=>setView("results")}>Results</button><button role="tab" aria-selected={view==="help"} className={view==="help"?"active":""} onClick={()=>setView("help")}>Needs your help{helpRows.length?` (${helpRows.length})`:""}</button></div>
 {view==="help"&&<>
  <p className="muted">These analyses couldn't produce a usable result. Each needs one thing from you. They leave this list once fixed.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {analyzeNext&&<p className="needs-help-banner" role="status">Saved the new description for <strong>{analyzeNext.company}</strong>, but it couldn't be queued automatically. <button className="primary" onClick={()=>void analyzeUpdated()}>Analyze updated job · API usage</button> <button className="text-action" onClick={()=>setAnalyzeNext(null)}>Not now</button></p>}
  {paste&&<form className="help-paste" onSubmit={e=>{e.preventDefault();void savePaste();}}>
   <h3>{paste.r.company} · {paste.r.title}</h3>
   <p>Paste just the job description: responsibilities and requirements. Leave out site menus, country lists and benefits boilerplate.</p>
   <textarea rows={14} value={paste.description} onChange={e=>setPaste({...paste,description:e.target.value})}/>
   <div className="row-actions"><button className="primary" type="submit" disabled={!!saving||paste.description.trim().length<200}>{saving?"Saving…":"Save description"}</button><button type="button" onClick={()=>setPaste(null)}>Cancel</button>{paste.description.trim().length<200&&<small>Paste at least a few paragraphs (200+ characters).</small>}</div>
  </form>}
  {!helpRows.length?<p>Nothing needs your help right now.</p>:<div className="discovery-table-scroll"><table className="discovery-table help-table" aria-label="Analyzed jobs that need your help"><thead><tr><th>Job</th><th>What's wrong</th><th>What to do</th><th>Action</th></tr></thead><tbody>
   {helpRows.map(({r,h})=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div>{r.url&&<div><a href={r.url} target="_blank" rel="noreferrer">View listing ↗</a></div>}</td><td>{h.issue}</td><td>{h.todo}</td>
    <td><div className="row-actions">
     {h.action==="rerun"&&<button className="primary" disabled={!!saving} onClick={()=>void rerun(r)}>{saving===r.job_id?"Queuing…":"Re-run analysis · API usage"}</button>}
     {h.action==="fix_description"&&<button className="primary" onClick={()=>void openPaste(r)}>{r.assessment.listing_changed?"Review description":"Paste description"}</button>}
     <button onClick={()=>void open(r.job_id)}>{h.action==="open"?"Open assessment":"Open details"}</button>
     <button disabled={!!rowSaving[r.job_id]} title="Not interested: remove from this list" onClick={()=>void passSaved(r,true)}>Pass</button>
    </div></td></tr>)}
  </tbody></table></div>}
 </>}
 {view==="results"&&<>
 {data&&<p role="status"><strong>{all.filter(r=>r.assessment.state==='COMPLETE').length} completed analyses</strong> · Scores stay visible even when a recommendation needs review or could not finish.</p>}
 <div className="analysis-toolbar"><label>Show<select value={filter} onChange={e=>setFilter(e.target.value)}>{filters.map(([id,label])=><option key={id} value={id}>{label}{data?` (${all.filter(r=>analysisFilter(r,id)).length})`:''}</option>)}</select></label><span>Deep assessment explains the fit. You decide whether to pursue the job.</span></div>
 <p className="discovery-clock">Most recently analyzed first.</p>
 {data?.settings.auto_assess_pause_reason&&<p role="status">{data.settings.auto_assess_pause_reason}</p>}
 {status.data?.refreshing&&data&&<p role="status">Updating saved results…</p>}
 {(status.error||status.data?.error)&&<p role="alert">{status.error?.message||status.data?.error}</p>}
 {all.some(r=>r.assessment.state==='COMPLETE'&&r.assessment.choice!=='SKIP'&&r.resume_approach?.state==='NOT_CHECKED')&&<button disabled={!!saving} onClick={()=>void checkApproaches()}>Check resume approach for existing results · API usage</button>}
 {saved.error&&<p role="alert">{saved.error.message}</p>}{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {!data?<div><p>{saved.error?'Could not load analyzed jobs. Your saved results are still available.':'Loading analyzed jobs…'}</p><button onClick={()=>void saved.refetch()}>Retry loading jobs</button></div>:!items.length?<div className="decision-empty"><h2>{pending>0?"No completed jobs match this filter yet":"No jobs in this view"}</h2><p>{pending>0?"Analysis is underway above. Choose All recommendations to see every completed result.":"No completed analyses have this recommendation. Choose All to see every completed result."}</p><button onClick={()=>setFilter("all")}>Show all recommendations</button><button onClick={onDiscover}>Go to New jobs</button></div>:<div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Analyzed jobs"><table className="discovery-table"><thead><tr><th>Company / role</th><th>Evidence score</th><th>Listing</th><th>Recommendation & why</th><th>Next action</th></tr></thead><tbody>
 {items.map(r=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div>{r.sponsorship&&<small className="sponsorship-blocked" title={r.sponsorship.quote}>No visa sponsorship: “{r.sponsorship.quote}”</small>}{r.resume&&<small className={`resume-status resume-status-${r.resume.state.toLowerCase()}`}>{r.resume.label}</small>}{r.last_scanned_at&&<small>Seen in scan #{r.last_scan_id} · {new Date(r.last_scanned_at).toLocaleString()}</small>}{r.assessment.assessed_at&&<small>Analyzed · {new Date(r.assessment.assessed_at).toLocaleString()}</small>}</td><td>{r.assessment.score===null?'—':`${r.assessment.score}/100`}{r.assessment.provisional&&<small>Provisional</small>}{r.assessment.state==='STALE'&&<small>Outdated</small>}</td><td>{r.url&&<a href={r.url} target="_blank" rel="noreferrer">View listing ↗</a>}</td><td><strong>{recommendation(r)}</strong>{r.assessment.explanation&&<RecommendationWhy value={r.assessment.explanation}/>}{!r.assessment.recommendation&&(r.assessment.recommendation_error||r.assessment.error)&&<p>{r.assessment.recommendation_error||r.assessment.error}</p>}<ResumeApproach jobId={r.job_id} value={r.resume_approach}/>{r.assessment.choice&&<small>Your choice: {r.assessment.choice==='SKIP'?'Passed':'Want to pursue'}</small>}</td><td className="role-action-cell"><div className="role-actions">
 {r.assessment.listing_changed&&r.opportunity_id?<button onClick={()=>onUpdatedJD(r.opportunity_id!)}>Review updated JD</button>:<>{r.assessment.state==='STALE'&&<button className="primary" disabled={!!saving} onClick={()=>void rerun(r)}>{saving===r.job_id?'Queuing…':'Re-run analysis · API usage'}</button>}<button onClick={()=>void open(r.job_id)}>{r.assessment.state==='COMPLETE'?'Review evidence':r.assessment.state==='STALE'?'View previous analysis':'Open unfinished analysis'}</button></>}
 {r.assessment.state!=='COMPLETE'&&<button disabled={!!rowSaving[r.job_id]} onClick={()=>void passSaved(r,r.assessment.choice!=='SKIP')}>{r.assessment.choice==='SKIP'?'Undo pass':'Pass'}</button>}
 {r.assessment.state==='COMPLETE'&&!r.assessment.listing_changed&&<><TailorResume jobId={r.job_id} label={`${r.company} · ${r.title}`}/><button disabled={!!rowSaving[r.job_id]} onClick={()=>void choose(r,r.assessment.choice==='SKIP'?'CASUAL':'SKIP')}>{rowSaving[r.job_id]?'Saving…':r.assessment.choice==='SKIP'?'Reconsider':'Pass'}</button></>}
 <button disabled={!!rowSaving[r.job_id]} onClick={()=>void keepResult(r)}>{r.kept?"Unkeep job":"Keep job"}</button><WritingHelp jobId={r.job_id} company={r.company} url={r.url} yc={!!r.yc_listing||isYCListing(r.url)}/><MarkApplied jobId={r.job_id} company={r.company}/></div></td></tr>)}
 </tbody></table></div>}
 </>}
 </section>;
}
