import {useEffect,useRef,useState} from 'react';
type Entry={id:string;revision:string;question:string;note:string;limit:number|null;unit:'words'|'characters';text:string;questions:string[];evidence:{source_id:string;title:string;quote:string}[];limit_exceeded?:boolean};
const blank=():Entry=>({id:crypto.randomUUID(),revision:'',question:'',note:'',limit:null,unit:'words',text:'',questions:[],evidence:[]});
export function ApplicationAnswer({jobId,company,url}:{jobId:string;company:string;url:string}){
 const dialog=useRef<HTMLDialogElement>(null);
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false),[items,setItems]=useState<Entry[]>([]),[entry,setEntry]=useState<Entry>(blank),[saved,setSaved]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dirty=loaded&&JSON.stringify(entry)!==saved;
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 function select(value:Entry){setEntry(value);setSaved(JSON.stringify(value));setNotice('');}
 async function request(action='',body?:object){const r=await fetch(`/api/jobs/${jobId}/application-answers${action}`,body?{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body)}:undefined);const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Could not complete the request. Check the question and length limit.');return v;}
 async function act(work:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await work();}catch(e){setError(e instanceof Error?e.message:'Could not complete this action.');}finally{setBusy(false);}}
 async function show(){setOpen(true);setLoaded(false);await act(async()=>{const v=await request();setItems(v.items);select(v.items[0]??blank());setLoaded(true);});}
 function close(){if(busy)return;if(dirty&&!window.confirm('Close without saving your answer edits?'))return;setOpen(false);}
 function switchEntry(id:string){if(dirty&&!window.confirm('Discard unsaved edits?'))return;select(items.find(x=>x.id===id)??blank());}
 function body(){return {id:entry.id,revision:entry.revision,question:entry.question,note:entry.note,limit:entry.limit,unit:entry.unit};}
 function accept(v:Entry){setItems(old=>[v,...old.filter(x=>x.id!==v.id)]);select(v);}
 async function save(){const v=await request('/save',{...body(),text:entry.text});accept(v);return v;}
 const count=entry.unit==='characters'?entry.text.length:entry.text.trim()?entry.text.trim().split(/\s+/).length:0;
 const over=!!entry.limit&&count>entry.limit;
 return <><button onClick={()=>void show()}>Answer an application question</button>
 <dialog ref={dialog} className="skills-dialog" aria-label={`Application questions for ${company}`} onCancel={e=>{e.preventDefault();close();}}>
 <div className="skills-dialog-heading"><h2>Application questions for {company}</h2><button disabled={busy} onClick={close}>Close</button></div>
 <p>Paste the employer’s question. Draft an answer from your experience, this role and your positioning notes. No tailored resume is required.</p>
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{!loaded&&!error&&<p>Loading saved answers…</p>}
 {loaded&&<>
 <div className="selection-actions"><label>Saved questions<select disabled={busy} value={items.some(x=>x.id===entry.id)?entry.id:''} onChange={e=>switchEntry(e.target.value)}><option value="">New question</option>{items.map(x=><option key={x.id} value={x.id}>{x.question.slice(0,100)}</option>)}</select></label><button disabled={busy} onClick={()=>switchEntry('')}>New question</button></div>
 <label>Application question<textarea rows={4} maxLength={6000} value={entry.question} disabled={busy} onChange={e=>setEntry({...entry,question:e.target.value})}/></label>
 <div className="selection-actions"><label>Length limit (optional)<input type="number" min={1} max={10000} value={entry.limit??''} disabled={busy} onChange={e=>setEntry({...entry,limit:e.target.value?Number(e.target.value):null})}/></label><label>Count by<select value={entry.unit} disabled={busy} onChange={e=>setEntry({...entry,unit:e.target.value as Entry['unit']})}><option value="words">Words</option><option value="characters">Characters</option></select></label></div>
 <label>Additional details<textarea rows={3} maxLength={6000} value={entry.note} disabled={busy} placeholder="Add relevant details or answer the clarification questions below." onChange={e=>setEntry({...entry,note:e.target.value})}/></label>
 <button disabled={busy||!entry.question.trim()||!!entry.limit&&(entry.limit<1||entry.limit>10000)} onClick={()=>{if(entry.text.trim()&&!window.confirm('Replace this answer with a new draft?'))return;void act(async()=>{accept(await request('/draft',body()));setNotice('Saved. Review the answer or any questions below.');});}}>{busy?'Working…':entry.text?'Redraft answer · API usage':'Draft answer · API usage'}</button>
 {entry.questions.length>0&&<section aria-label="Details needed"><h3>A few details are needed</h3><ul>{entry.questions.map((q,i)=><li key={i}>{q}</li>)}</ul><p>Add these details above, then draft again.</p></section>}
 <label>Your answer<textarea rows={9} maxLength={16000} value={entry.text} disabled={busy} onChange={e=>setEntry({...entry,text:e.target.value,evidence:[]})}/></label>
 <p role={over?'alert':undefined}>{count}{entry.limit?` / ${entry.limit}`:''} {entry.unit}{over?' — over the limit; shorten before submitting.':''}</p>
 {entry.evidence.length>0&&<details><summary>Source evidence</summary>{entry.evidence.map((r,i)=><div key={i}><strong>{r.title}</strong><blockquote>{r.quote}</blockquote></div>)}</details>}
 <div className="selection-actions"><button disabled={busy||!dirty||!entry.question.trim()} onClick={()=>void act(async()=>{await save();setNotice('Answer saved.');})}>Save edits</button><button disabled={busy||!entry.text.trim()} onClick={()=>void act(async()=>{if(dirty)await save();await navigator.clipboard.writeText(entry.text);setNotice('Copied. Paste it into the application.');})}>Copy answer</button>{/^https?:\/\//.test(url)&&<a href={url} target="_blank" rel="noreferrer">Open application ↗</a>}</div>
 </>}
 </dialog></>;
}
