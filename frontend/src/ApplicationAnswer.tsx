import {useEffect,useRef,useState} from 'react';
type Entry={id:string;revision:string;question:string;note:string;limit:number|null;unit:'words'|'characters';text:string;questions:string[];evidence:{source_id:string;title:string;quote:string}[];evidence_dropped?:number;limit_exceeded?:boolean;reusable?:boolean|null;reused_from?:string;reviewed?:boolean};
type Match={job_id:string;answer_id:string;company:string;question:string;text:string;score:number;updated_at:string;length:number;unit:string;over_limit:boolean;stories_changed:boolean};
const blank=():Entry=>({id:crypto.randomUUID(),revision:'',question:'',note:'',limit:null,unit:'words',text:'',questions:[],evidence:[]});
export function ApplicationAnswer({jobId,company,url,autoOpen=false}:{jobId:string;company:string;url:string;autoOpen?:boolean}){
 const dialog=useRef<HTMLDialogElement>(null);
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false),[items,setItems]=useState<Entry[]>([]),[entry,setEntry]=useState<Entry>(blank),[saved,setSaved]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[where,setWhere]=useState<'draft'|'actions'>('draft');
 const dirty=loaded&&JSON.stringify(entry)!==saved;
 const [matches,setMatches]=useState<{specific:boolean;matches:Match[]}|null>(null);
 // Free lookup of your saved answers to similar general questions in other applications.
 useEffect(()=>{setMatches(null);if(!open||!loaded||entry.text.trim()||entry.question.trim().length<12)return;let live=true;
  const timer=setTimeout(()=>{void fetch(`/api/jobs/${jobId}/application-answers/matches`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({question:entry.question,limit:entry.limit,unit:entry.unit})}).then(r=>r.ok?r.json():null).then(v=>{if(live)setMatches(v);}).catch(()=>{});},700);
  return()=>{live=false;clearTimeout(timer);};},[open,loaded,entry.question,entry.limit,entry.unit,!entry.text.trim()]);
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 useEffect(()=>{if(autoOpen)void show();},[]);
 function select(value:Entry){setEntry(value);setSaved(JSON.stringify(value));setNotice('');}
 async function request(action='',body?:object){const r=await fetch(`/api/jobs/${jobId}/application-answers${action}`,body?{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body)}:undefined);const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Could not complete the request. Check the question and length limit.');return v;}
 async function act(work:()=>Promise<void>,at:'draft'|'actions'='actions'){setWhere(at);setBusy(true);setError('');setNotice('');try{await work();}catch(e){setError(e instanceof Error?e.message:'Could not complete this action.');}finally{setBusy(false);}}
 async function show(){setOpen(true);setLoaded(false);await act(async()=>{const v=await request();setItems(v.items);select(v.items[0]??blank());setLoaded(true);});}
 function close(){if(busy)return;if(dirty&&!window.confirm('Close without saving your answer edits?'))return;setOpen(false);}
 function switchEntry(id:string){if(dirty&&!window.confirm('Discard unsaved edits?'))return;select(items.find(x=>x.id===id)??blank());}
 function body(){return {id:entry.id,revision:entry.revision,question:entry.question,note:entry.note,limit:entry.limit,unit:entry.unit,reusable:entry.reusable??null};}
 function accept(v:Entry){setItems(old=>[v,...old.filter(x=>x.id!==v.id)]);select(v);}
 async function save(){const v=await request('/save',{...body(),text:entry.text});accept(v);return v;}
 const count=entry.unit==='characters'?entry.text.length:entry.text.trim()?entry.text.trim().split(/\s+/).length:0;
 const over=!!entry.limit&&count>entry.limit;
 const status=<>{loaded&&error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}</>;
 return <><button onClick={()=>void show()}>Answer an application question</button>
 <dialog ref={dialog} className="skills-dialog application-answer-dialog" aria-label={`Application questions for ${company}`} onCancel={e=>{e.preventDefault();close();}}>
 <div className="skills-dialog-heading"><h2>Application questions for {company}</h2><button disabled={busy} onClick={close}>Close</button></div>
 <p>Paste the employer’s question. Draft an answer from your experience, this role and your positioning notes. No tailored resume is required.</p>
 {!loaded&&error&&<p role="alert">{error}</p>}{!loaded&&!error&&<p>Loading saved answers…</p>}
 {loaded&&<>
 <div className="answer-saved-row"><label>Saved questions<select disabled={busy} value={items.some(x=>x.id===entry.id)?entry.id:''} onChange={e=>switchEntry(e.target.value)}><option value="">New question</option>{items.map(x=><option key={x.id} value={x.id}>{x.question.slice(0,100)}</option>)}</select></label><button disabled={busy} onClick={()=>switchEntry('')}>New question</button></div>
 <label>Application question<textarea rows={4} maxLength={6000} value={entry.question} disabled={busy} onChange={e=>setEntry({...entry,question:e.target.value})}/></label>
 <div className="answer-limit-row"><label>Length limit (optional)<input type="number" min={1} max={10000} value={entry.limit??''} disabled={busy} onChange={e=>setEntry({...entry,limit:e.target.value?Number(e.target.value):null})}/></label><label>Count by<select value={entry.unit} disabled={busy} onChange={e=>setEntry({...entry,unit:e.target.value as Entry['unit']})}><option value="words">Words</option><option value="characters">Characters</option></select></label></div>
 {matches?.specific&&<p className="answer-reuse-note">This question is about this company or role, so it gets a fresh answer.</p>}
 {!!matches?.matches.length&&<section className="answer-reuse" aria-label="Similar answers you saved"><h3>You've answered this before</h3>
  {matches.matches.map(m=><div key={m.job_id+m.answer_id} className="answer-reuse-item"><p><strong>{m.company}</strong> · {m.updated_at?new Date(m.updated_at).toLocaleDateString():''} · {Math.round(m.score*100)}% similar</p><p className="answer-reuse-question">{m.question}</p><blockquote>{m.text}</blockquote>
   {m.over_limit&&<p role="alert">{m.length} {m.unit}; this box allows {entry.limit}. Shorten it after using it, or draft new.</p>}
   {m.stories_changed&&<small>Your stories changed after this answer was written. Check the facts are still current.</small>}
   <button className="primary" disabled={busy} onClick={()=>void act(async()=>{const v=await request('/save',{...body(),text:m.text,reused_from:`${m.job_id}:${m.answer_id}`});accept(v);setNotice(`Used your ${m.company} answer (no API usage). Edit it below if needed, then save.`);},'draft')}>Use this answer · free</button></div>)}
 </section>}
 <label>Additional details<textarea rows={3} maxLength={6000} value={entry.note} disabled={busy} placeholder="Add relevant details or answer the clarification questions below." onChange={e=>setEntry({...entry,note:e.target.value})}/></label>
 <button disabled={busy||!entry.question.trim()||!!entry.limit&&(entry.limit<1||entry.limit>10000)} onClick={()=>{if(entry.text.trim()&&!window.confirm('Replace this answer with a new draft?'))return;void act(async()=>{const v=await request('/draft',body());accept(v);setNotice(v.evidence_dropped?`New draft ready below. ${v.evidence_dropped} source excerpt(s) could not be matched to your documents and were hidden — double-check those facts.`:'New draft ready below. Review the answer and any questions.');},'draft');}}>{busy?'Working…':entry.text?'Redraft answer · API usage':'Draft answer · API usage'}</button>
 {where==='draft'&&status}
 {entry.questions.length>0&&<section aria-label="Details needed"><h3>{entry.text.trim()?'Confirm these details':'Details needed to draft an answer'}</h3><ul>{entry.questions.map((q,i)=><li key={i}>{q}</li>)}</ul><p>{entry.text.trim()?'Your draft is below. Confirm these details in Additional details if needed, then redraft.':'Add these details above, then draft again.'}</p></section>}
 <label>Your answer<textarea rows={9} maxLength={16000} value={entry.text} disabled={busy} onChange={e=>setEntry({...entry,text:e.target.value,evidence:[]})}/></label>
 <p role={over?'alert':undefined}>{count}{entry.limit?` / ${entry.limit}`:''} {entry.unit}{over?' — over the limit; shorten before submitting.':''}</p>
 {entry.evidence.length>0&&<details><summary>Source evidence</summary>{entry.evidence.map((r,i)=><div key={i}><strong>{r.title}</strong><blockquote>{r.quote}</blockquote></div>)}</details>}
 <label className="answer-reuse-toggle"><input type="checkbox" disabled={busy} checked={entry.reusable!==false} onChange={e=>setEntry({...entry,reusable:e.target.checked?null:false})}/><span>Reuse for other jobs<small>Company-specific questions are never reused.</small></span></label>
 <div className="selection-actions"><button disabled={busy||!dirty||!entry.question.trim()} onClick={()=>void act(async()=>{await save();setNotice('Answer saved.');})}>Save edits</button><button disabled={busy||!entry.text.trim()} onClick={()=>void act(async()=>{if(dirty||entry.reviewed===false)await save();await navigator.clipboard.writeText(entry.text);setNotice('Copied. Paste it into the application.');})}>Copy answer</button>{/^https?:\/\//.test(url)&&<a href={url} target="_blank" rel="noreferrer">Open application ↗</a>}</div>
 {where==='actions'&&status}
 </>}
 </dialog></>;
}
