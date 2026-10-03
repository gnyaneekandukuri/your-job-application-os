import { useEffect, useState } from "react";
import { queueGroup, nextAction, type Result } from "./assessmentQueue";
import { ScanReport, type ScanReportData } from "./ScanReport";
type State = {items:Result[];queued_count:number;settings:{auto_assess:boolean;auto_assess_pause_reason?:string};scans:ScanReportData[]};
const tabs=[["decision","Decide next"],["shortlist","My shortlist"],["attention","Needs attention"],["running","In progress"],["skipped","Passed over"],["complete","All completed"]];
const belongs=(r:Result,tab:string)=>tab==='complete'?r.assessment.state==='COMPLETE'&&!!r.assessment.recommendation:tab==='previous'?r.assessment.state==='STALE':queueGroup(r)===tab;
export function AssessmentResults({onOpen,onDiscover,onUpdatedJD,initialTab="decision"}:{initialTab?:string;onUpdatedJD:(id:string)=>void;onDiscover:()=>void;onOpen:(id:string)=>Promise<void>}) {
 const [data,setData]=useState<State|null>(null),[tab,setTab]=useState(initialTab),[error,setError]=useState('');
 useEffect(()=>{let live=true;let timer:ReturnType<typeof setTimeout>;const controller=new AbortController();
 const load=async()=>{try{const r=await fetch('/api/assessments',{signal:controller.signal});if(!r.ok)throw new Error('Could not load your next steps.');const value=await r.json();if(live){setData(value);setError('');}}catch(e){if(live)setError(String(e));}finally{if(live)timer=setTimeout(()=>void load(),document.hidden?60000:15000);}};
 void load();return()=>{live=false;controller.abort();clearTimeout(timer);};},[]);
 const all=data?.items??[];
 const items=all.filter(r=>belongs(r,tab)).sort((a,b)=>(b.assessment.score??-1)-(a.assessment.score??-1));
 const open=async(id:string)=>{try{await onOpen(id);}catch(e){setError(String(e));}};
 return <section className="panel discovery decision-home">
 <div className="section-heading"><div><div className="eyebrow">START HERE</div><h1>My next steps</h1><p>Review a recommendation. Choose the roles you want. Work from your shortlist.</p></div><button onClick={onDiscover}>Discover more roles</button></div>
 {error&&<p role="alert">{error}</p>}
 {data&&<ScanReport scans={data.scans??[]} onOpen={open}/>}
 {data?.settings.auto_assess_pause_reason&&<p role="status">{data.settings.auto_assess_pause_reason}</p>}
 <div className="opportunity-filters decision-tabs" aria-label="Your next steps">{tabs.map(([id,label])=><button key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{label} ({data?all.filter(r=>belongs(r,id)).length:'…'})</button>)}</div>
 {tab==='decision'&&<p>These roles have an assessment. Open one to review the evidence and choose <strong>Attack hard, Casual, or Skip</strong>. Changed listings return here for review.</p>}
 {tab==='shortlist'&&<p><strong>Your chosen roles to pursue.</strong> Prepare your resume and apply through the listing when ready. Resume tailoring and application tracking are not built yet.</p>}
 {tab==='attention'&&<p>Unfinished or outdated assessments are here. They are not ready-to-apply recommendations.</p>}
 {tab==='running'&&<p>{data?.queued_count??0} queued · {all.filter(r=>queueGroup(r)==='running').length} running. Queued work has not completed yet.</p>}
 {!data?<p>Loading your next steps…</p>:!items.length?<div className="decision-empty"><h2>{tab==='shortlist'?'Your shortlist is empty':tab==='decision'?'No roles waiting for a decision':tab==='running'?'No assessments running':'No roles in this view'}</h2><p>{tab==='shortlist'?'Choose Attack hard or Casual after reviewing a role to place it here.':tab==='decision'?'Discover roles to assess, or review unfinished work under Needs attention.':'Use the other tabs to review your saved work.'}</p><button onClick={tab==='shortlist'?()=>setTab('decision'):onDiscover}>{tab==='shortlist'?'Review recommendations':'Discover more roles'}</button></div>:<div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Roles and next actions"><table className="discovery-table"><thead><tr><th>Company / role</th><th>Evidence score</th><th>Listing</th><th>Recommendation / your choice</th><th>Your next action</th></tr></thead><tbody>
 {items.map(r=><tr key={r.job_id}><td><strong>{r.company}</strong><div>{r.title}</div></td><td>{r.assessment.score===null?'—':`${r.assessment.score}/100`}{r.assessment.provisional&&<small>Provisional</small>}{r.assessment.state==='STALE'&&<small>Outdated result</small>}</td><td>{r.url&&<a href={r.url} target="_blank" rel="noreferrer">{tab==='shortlist'?'Open application listing ↗':'View listing ↗'}</a>}</td><td>{r.assessment.recommendation?.replaceAll('_',' ')??'Not available'}<small>Your choice: {r.assessment.choice?.replaceAll('_',' ')??'Not decided'}</small></td><td>{r.assessment.listing_changed&&<p>The JD changed since this assessment.</p>}{queueGroup(r)==='attention'&&<details><summary>Why it needs attention</summary><p>{r.assessment.error||r.assessment.recommendation_error||r.assessment.stage}</p></details>}<button onClick={()=>r.assessment.listing_changed&&r.opportunity_id?onUpdatedJD(r.opportunity_id):void open(r.job_id)}>{nextAction(r)}</button></td></tr>)}
 </tbody></table></div>}
 </section>;
}
