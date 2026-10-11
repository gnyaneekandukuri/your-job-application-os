import {useEffect,useRef,useState} from 'react';
type Saved={text:string;note:string;revision:string;question?:string;kind?:string};
export function isYCListing(url:string){try{return ['workatastartup.com','www.workatastartup.com','ycombinator.com','www.ycombinator.com'].includes(new URL(url).hostname);}catch{return false;}}
export function TeamMessage({jobId,company,url,kind='introduction'}:{jobId:string;company:string;url:string;kind?:'introduction'|'why_company'}){
 const companyAnswer=kind==='why_company';
 const title=companyAnswer?`Why ${company}?`:`Message the team at ${company}`;
 const [question,setQuestion]=useState(`What excites you about ${company}?`);
 const dialog=useRef<HTMLDialogElement>(null);
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false);
 const [saved,setSaved]=useState<Saved>({text:'',note:'',revision:''}),[text,setText]=useState(''),[note,setNote]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dirty=text!==saved.text||note!==saved.note||(companyAnswer&&question!==(saved.question||`What excites you about ${company}?`));
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 async function request(action='',body?:unknown):Promise<Saved>{
  const r=await fetch(`/api/jobs/${jobId}/team-message${action}?kind=${kind}`,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({...body as object,kind,question})});
  if(r.status===404)throw Error('Message drafting needs the updated server. Your current work is safe.');
  const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Could not save the message.');if(companyAnswer&&v.kind!=='why_company')throw Error('This feature needs a server restart after the current scan finishes.');return v;
 }
 function accept(v:Saved){setSaved(v);setText(v.text);setNote(v.note);setQuestion(v.question||`What excites you about ${company}?`);}
 async function act(work:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await work();}catch(e){setError(e instanceof Error?e.message:'Could not complete this action.');}finally{setBusy(false);}}
 async function show(){setOpen(true);setLoaded(false);await act(async()=>{accept(await request());setLoaded(true);});}
 function close(){if(busy)return;if(dirty&&!window.confirm('Close without saving your message edits?'))return;setOpen(false);}
 async function save(){const v=await request('/save',{text,note,revision:saved.revision});accept(v);return v;}
 return <><button onClick={()=>void show()}>{companyAnswer?'Why this company?':'Message the team'}</button><dialog ref={dialog} className="skills-dialog" aria-label={title} onCancel={e=>{e.preventDefault();close();}}>
  <div className="skills-dialog-heading"><h2>{title}</h2><button disabled={busy} onClick={close}>Close</button></div>
  <p>{companyAnswer?'Draft an answer using this job and your experience. Edit it before pasting into the application.':'Introduce yourself and explain why this role interests you. Personalize the draft before sending.'}</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!loaded&&!error&&<p>Loading your message…</p>}
  {loaded&&<>{companyAnswer&&<label>Application question<textarea value={question} maxLength={1000} rows={2} disabled={busy} onChange={e=>setQuestion(e.target.value)}/></label>}<label>Your personal note<textarea value={note} maxLength={3000} rows={3} disabled={busy} onChange={e=>setNote(e.target.value)} placeholder="What interests you about this company or what you're looking for"/></label>
  <button disabled={busy} onClick={()=>{if(text.trim()&&!window.confirm('Replace the current message with a new draft?'))return;void act(async()=>{accept(await request('/draft',{note,revision:saved.revision}));setNotice('Draft saved. Edit it to sound like you.');});}}>{busy?'Working…':text?'Redraft · API usage':companyAnswer?'Draft answer · API usage':'Draft message · API usage'}</button>
  <label>{companyAnswer?'Your answer':'Your message'}<textarea value={text} maxLength={6000} rows={9} disabled={busy} onChange={e=>setText(e.target.value)}/></label>
  <small>{text.trim()?text.trim().split(/\s+/).length:0} words</small>
  <div className="selection-actions"><button disabled={busy||!dirty} onClick={()=>void act(async()=>{await save();setNotice('Message saved.');})}>Save edits</button>
  <button disabled={busy||!text.trim()} onClick={()=>void act(async()=>{if(dirty)await save();await navigator.clipboard.writeText(text);setNotice('Copied. Paste it into the application when ready.');})}>{companyAnswer?'Copy answer':'Copy message'}</button>
  <a href={url} target="_blank" rel="noreferrer">Open application ↗</a></div></>}
 </dialog></>;
}
