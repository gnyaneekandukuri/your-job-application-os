import {useState} from 'react';
import type {ResumeApproach as Advice} from './assessmentQueue';

export function ResumeApproach({jobId,value}:{jobId:string;value?:Advice}) {
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 async function check(){setBusy(true);setError('');try{
  const r=await fetch(`/api/jobs/${jobId}/resume-approach`,{method:'POST',headers:{'X-Local-Review':'1'}});
  if(!r.ok){const body=await r.json();throw Error(body.detail||'Could not check resume approach.');}
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const labels={USE_MASTER:'Use master',SMALL_EDITS:'Small edits',TAILOR:'Tailor'};
 return <section className="resume-approach" aria-label="Resume approach">
 {value?.state==='COMPLETE'&&value.approach?<>
  <strong>{labels[value.approach]}</strong><p>{value.reason}</p>
  {!!value.changes?.length&&<details><summary>Suggested changes ({value.changes.length})</summary><ul>{value.changes.map((c,i)=><li key={i}><strong>{c.job_need}</strong><p>{c.action}</p><small>Resume point: {c.resume_point}</small></li>)}</ul></details>}
  {!!value.experience_gaps?.length&&<details><summary>Gaps rewriting cannot resolve</summary><ul>{value.experience_gaps.map((g,i)=><li key={i}>{g}</li>)}</ul></details>}
 </>:value?.state==='QUEUED'||value?.state==='RUNNING'?<p role="status">Checking resume approach…</p>:<>
  <p>{value?.reason||'Resume approach not checked yet.'}</p>
  {value?.state!=='UNAVAILABLE'&&<button disabled={busy} onClick={()=>void check()}>{busy?'Queuing…':value?.state==='ERROR'?'Retry resume approach · API usage':'Check resume approach · API usage'}</button>}
 </>}
 {error&&<p role="alert">{error}</p>}
 </section>;
}
