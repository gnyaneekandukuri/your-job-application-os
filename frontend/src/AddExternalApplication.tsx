import {useState} from 'react';
const localDate=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
// Jobs found and applied to outside this app: no saved JD or resume needed.
export function AddExternalApplication({onAdded}:{onAdded:()=>void}){
 const blank={company:'',title:'',url:'',location:'',applied_date:localDate(new Date()),notes:''};
 const [form,setForm]=useState(blank),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const set=(k:keyof typeof blank)=>(e:{target:{value:string}})=>setForm(f=>({...f,[k]:e.target.value}));
 async function submit(){
  setBusy(true);setError('');setNotice('');
  try{
   const r=await fetch('/api/applications/external',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({...form,url:form.url.trim()||null})});
   const v=await r.json();
   if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Check the link: it must be a full web address, or leave it empty.');
   setNotice(`${form.company} · ${form.title} added for ${form.applied_date}.`);setForm(blank);onAdded();
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 return <details className="external-application"><summary>Add a job you applied to elsewhere</summary>
  <form onSubmit={e=>{e.preventDefault();void submit();}}>
   <label>Company<input required maxLength={200} value={form.company} onChange={set('company')}/></label>
   <label>Role title<input required maxLength={200} value={form.title} onChange={set('title')}/></label>
   <label>Job link (optional)<input type="url" value={form.url} onChange={set('url')} placeholder="https://"/></label>
   <label>Location (optional)<input maxLength={200} value={form.location} onChange={set('location')}/></label>
   <label>Applied on<input type="date" required value={form.applied_date} onChange={set('applied_date')}/></label>
   <label>Notes (optional)<input maxLength={5000} value={form.notes} onChange={set('notes')}/></label>
   <button className="primary" disabled={busy}>Add to applied jobs</button>
  </form>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </details>;
}
