import {useEffect,useRef,useState} from 'react';
import {useQueryClient} from '@tanstack/react-query';
export function MarkApplied({jobId,opportunityId,company,onRecorded}:{jobId?:string;opportunityId?:string;company:string;onRecorded?:()=>void}){
 const query=useQueryClient(),dialog=useRef<HTMLDialogElement>(null);
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notes,setNotes]=useState('');
 const [date,setDate]=useState(()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;});
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 async function confirm(){setBusy(true);setError('');try{
  const path=jobId?`/api/jobs/${jobId}`:`/api/discovery/${opportunityId}`;
  const r=await fetch(path+'/applications/manual',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({confirmed:true,applied_date:date,notes})});
  const v=await r.json();if(!r.ok)throw Error(r.status===404?'This action needs the updated server.':typeof v.detail==='string'?v.detail:'Could not record application.');
  query.setQueryData<{job_id:string;url:string}[]>(['applications'],rows=>[...(rows||[]).filter(a=>a.job_id!==v.job_id),v]);
  void query.invalidateQueries({queryKey:['job-list']});void query.invalidateQueries({queryKey:['job-list-status']});
  setOpen(false);onRecorded?.();
 }catch(e){setError(e instanceof Error?e.message:'Could not save application.');}finally{setBusy(false);}}
 return <><button className="applied-action" onClick={()=>{setError('');setOpen(true);}}>I applied</button><dialog ref={dialog} className="skills-dialog" aria-label={`Record application to ${company}`} onCancel={e=>{if(busy)e.preventDefault();else setOpen(false);}}>
 <h2>Applied to {company}</h2><p>Record an application you already submitted. No tailored resume is required.</p>
 <form onSubmit={e=>{e.preventDefault();void confirm();}}><fieldset disabled={busy}>
 <label>Application date<input type="date" required value={date} onChange={e=>setDate(e.target.value)}/></label>
 <label>Notes<textarea rows={3} maxLength={5000} value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Resume used or anything you want to remember"/></label>
 {error&&<p role="alert">{error}</p>}
 <div className="selection-actions"><button type="submit" className="primary">{busy?'Saving…':'Confirm I applied'}</button><button type="button" onClick={()=>setOpen(false)}>Cancel</button></div>
 </fieldset></form></dialog></>;
}
