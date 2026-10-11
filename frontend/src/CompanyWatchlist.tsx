import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

type Company = {company:string;careers_url:string|null;reason:string;roles:string};
const empty:Company={company:'',careers_url:'',reason:'',roles:''};
async function request(data?:Company & {remove?:boolean}):Promise<Company[]> {
 const response=await fetch('/api/discovery/watchlist',data?{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({...data,careers_url:data.careers_url||null})}:undefined);
 if(!response.ok)throw new Error('Could not save the company watchlist. Please try again.');
 return response.json();
}
export function CompanyWatchlist({company,onClose}:{company:string|null;onClose:()=>void}) {
 const queryClient=useQueryClient();
 const list=useQuery({queryKey:['company-watchlist'],queryFn:()=>request()});
 const dialog=useRef<HTMLDialogElement>(null);
 const [draft,setDraft]=useState<Company>(empty),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{if(company!==null){setDraft(list.data?.find(c=>c.company.toLowerCase()===company.toLowerCase())??{...empty,company});setError('');dialog.current?.showModal();}else dialog.current?.close();},[company]);
 async function save(remove=false){setBusy(true);setError('');try{await request({...draft,remove});await queryClient.invalidateQueries({queryKey:['company-watchlist']});await queryClient.invalidateQueries({queryKey:['job-list-status']});dialog.current?.close();onClose();}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <>
  <details className="discovery-preferences"><summary>Companies to watch ({list.data?.length??0})</summary>
   <p>Keep promising companies here after old jobs are removed. Future jobs from these companies are marked in your scan results; their priority still depends on the role.</p>
   {list.error&&<p role="alert">Could not load the company watchlist.</p>}
   {list.data?.map(c=><div key={c.company} className="discovery-run"><strong>{c.company}</strong>{c.careers_url&&<> · <a href={c.careers_url} target="_blank" rel="noreferrer">Careers page ↗</a></>}<p>{c.reason}</p>{c.roles&&<p>Roles to watch: {c.roles}</p>}<button onClick={()=>{setDraft(c);setError('');dialog.current?.showModal();}}>Edit company</button></div>)}
   <button onClick={()=>{setDraft(empty);setError('');dialog.current?.showModal();}}>Add company</button>
  </details>
  <dialog ref={dialog} onCancel={onClose} style={{maxWidth:620,width:'90%'}}>
   <form onSubmit={e=>{e.preventDefault();void save();}}>
    <h2>Watch a company</h2>
    <label>Company<input required maxLength={200} value={draft.company} onChange={e=>setDraft({...draft,company:e.target.value})}/></label>
    <label>Careers page (optional)<input type="url" value={draft.careers_url??''} onChange={e=>setDraft({...draft,careers_url:e.target.value})}/></label>
    <label>Why it is a good fit<textarea maxLength={2000} value={draft.reason} onChange={e=>setDraft({...draft,reason:e.target.value})}/></label>
    <label>Roles to watch<input maxLength={1000} value={draft.roles} onChange={e=>setDraft({...draft,roles:e.target.value})}/></label>
    {error&&<p role="alert" className="error">{error}</p>}
    <div className="actions"><button disabled={busy||!draft.company.trim()}>Save company</button><button type="button" disabled={busy} onClick={()=>{dialog.current?.close();onClose();}}>Cancel</button>{list.data?.some(c=>c.company===draft.company)&&<button type="button" disabled={busy} onClick={()=>void save(true)}>Remove from watchlist</button>}</div>
   </form>
  </dialog>
 </>;
}
