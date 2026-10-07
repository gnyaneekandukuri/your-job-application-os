import { useEffect, useRef, useState } from 'react';

type Block={kind:'heading'|'contact'|'role'|'bullet'|'text';text:string;citations:{source_id:string;quote:string}[];reason:string};
type RecordValue={state:string;stage:string;error:string;revision:string;framework:string;policy_version:string;draft:{blocks:Block[];questions:string[]}|null;checks:{findings:{block_index:number;supported:boolean;reason:string}[]}|null;context:{sources:{id:string;title:string;text:string;employer?:string;start_line?:number}[]}};
type View={framework:string;base_resume:string;base_title:string;configured:boolean;record:RecordValue|null};
const styles=[['PAR','Recommended · Project, Action, Result'],['XYZ','Metrics-focused · Accomplishment, measurement, method'],['STAR','Context-focused · Concise STAR'],['EXISTING','Keep my existing style']];
const examples:Record<string,string>={PAR:'Show the work, your contribution, and the supported outcome. Numbers are optional.',XYZ:'Lead with a measured accomplishment, then explain how you achieved it. Use only documented metrics.',STAR:'Briefly explain the problem, your action, and the result.',EXISTING:'Preserve your voice and section structure while improving relevance and clarity.'};
export function TailorResume({jobId,label}:{jobId:string;label?:string}) {
 const dialog=useRef<HTMLDialogElement>(null);
 const [open,setOpen]=useState(false),[view,setView]=useState<View|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [framework,setFramework]=useState('PAR'),[base,setBase]=useState(''),[remember,setRemember]=useState(false),[editing,setEditing]=useState(false),[blocks,setBlocks]=useState<Block[]>([]),[confirmed,setConfirmed]=useState(false);
 const path=`/api/jobs/${jobId}/tailoring`,record=view?.record,running=record?.state==='RUNNING';
 async function request(suffix='',body?:unknown):Promise<View>{
  const response=await fetch(path+suffix,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Could not save this resume. Check the fields and try again.');return data;
 }
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 useEffect(()=>{if(!open)return;let live=true;void request().then(data=>{if(live){setView(data);setFramework(data.record?.framework||data.framework);setBase(data.record?.context.sources[0]?.text||data.base_resume);}}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[open,jobId]);
 useEffect(()=>{if(!open||!running)return;let live=true;const timer=setInterval(()=>{void request().then(data=>{if(live)setView(data);}).catch(e=>{if(live)setError(String(e));});},2500);return()=>{live=false;clearInterval(timer);};},[open,running,jobId]);
 async function act(suffix:string,body:unknown){if(busy)return;setBusy(true);setError('');setConfirmed(false);try{setView(await request(suffix,body));setEditing(false);}catch(e){setError(String(e));}finally{setBusy(false);}}
 function change(i:number,text:string){setBlocks(old=>old.map((b,j)=>i===j?{...b,text}:b));}
 async function download(){setBusy(true);setError('');try{const response=await fetch(path+`/export?revision=${encodeURIComponent(record!.revision)}`);if(!response.ok){const data=await response.json();throw new Error(data.detail||'Export failed.');}const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='tailored-resume.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <><button onClick={()=>{setError('');setEditing(false);setConfirmed(false);setOpen(true);}}>Tailor resume</button>
 <dialog ref={dialog} className="skills-dialog tailoring-dialog" onCancel={()=>setOpen(false)} aria-label="Tailor resume">
 <div className="skills-dialog-heading"><div><h2>Tailor your resume</h2><p>{label||"Choose an approach, review the evidence, then approve your draft."}</p></div><button onClick={()=>setOpen(false)} aria-label="Close resume tailoring">Close</button></div>
 {error&&<p role="alert">{error}</p>}
 {!view?<p>Loading your resume…</p>:<>
 <details open={!record}><summary>Resume and writing approach</summary>
 <label>Writing approach<select value={framework} disabled={busy||running} onChange={e=>setFramework(e.target.value)}>{styles.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label><p>{examples[framework]}</p>
 <label className="tailor-checkbox"><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)}/> Remember this approach for future jobs</label>
 <label>Base resume<textarea rows={10} maxLength={22000} value={base} disabled={busy||running} onChange={e=>setBase(e.target.value)} placeholder="Paste your resume here, or add a resume in Your context → Add skills."/></label>
 <p>This uses your JD, base resume, relevant saved evidence, and the resume skill. Drafting and claim checking use OpenAI; costs appear in API costs. Your master resume stays unchanged.</p>
 <button className="primary" disabled={busy||running||!view.configured||!base.trim()} onClick={()=>void act('',{consent_to_send:true,framework,base_resume:base,remember,retry:false})}>Prepare draft · API usage</button>
 {!view.configured&&<p role="alert">Connect your OpenAI API key to prepare a draft.</p>}
 </details>
 <details><summary>Ground rules included</summary><p>PAR by default, with XYZ, concise STAR, or your existing style. Every claim needs candidate evidence. Preserve ownership, dates, titles, numbers, and project context. Never invent qualifications or copy requirements as experience. Use plain language, relevant accomplishments, and readable formatting. Review changes before approval; no automatic applications.</p><p>Citation checks and an AI claim review help catch errors; they are not independent verification of your experience.</p></details>
 {record&&<><p role="status"><strong>{record.state==='APPROVED'?'Approved resume':['ERROR','INTERRUPTED'].includes(record.state)?'Preparation stopped — draft saved':record.stage||record.state}</strong> · {record.framework} · Rules v{record.policy_version}</p>
 {running&&<p>Preparation continues on the server if you close this window or refresh.</p>}
 {record.error&&<p role="alert">{record.error}</p>}
 {['ERROR','INTERRUPTED'].includes(record.state)&&!editing&&<button disabled={busy} onClick={()=>void act('',{consent_to_send:true,framework:record.framework,base_resume:record.context.sources[0].text,retry:true})}>Retry unfinished preparation · API usage</button>}
 {record.draft&&<>
 {record.draft.questions.length>0&&<details><summary>Questions and missing details ({record.draft.questions.length})</summary><ul>{record.draft.questions.map((q,i)=><li key={i}>{q}</li>)}</ul><p>Add factual details to the relevant experience in Your context, then reassess before generating a new draft.</p></details>}
 <div className="tailoring-review-heading"><div><h3>Your tailored draft</h3><p>{running?'Draft prepared; claim checks are still running. You can read the wording below.':['ERROR','INTERRUPTED'].includes(record.state)?'Your draft is saved, but verification did not finish. Review the error above. Expand a source to read the supporting quote.':'Review the tailored wording below, then approve when it is ready. Expand a source to read the supporting quote.'}</p></div>{!running&&!editing&&<button disabled={busy} onClick={()=>{setBlocks(record.draft!.blocks.map(b=>({...b})));setEditing(true);setConfirmed(false);}}>Edit draft</button>}</div>
 <div className="tailoring-table-wrap"><table className="tailoring-review-table" aria-label="Tailored resume and supporting evidence">
 <colgroup><col className="tailoring-company-col"/><col className="tailoring-point-col"/><col className="tailoring-evidence-col"/></colgroup>
 <thead><tr><th scope="col">Company / section</th><th scope="col">Resume point</th><th scope="col">Evidence</th></tr></thead>
 <tbody>{(()=>{let section='Contact details',company='';return (editing?blocks:record.draft!.blocks).map((block,i)=>{
  if(block.kind==='heading'){section=block.text;company='';}
  if(block.kind==='role')company=block.text.split('|')[0].trim();
  const finding=!editing?record.checks?.findings.find(f=>f.block_index===i):undefined;
  if(block.kind==='heading'&&!editing)return <tr key={i} className="tailoring-section-row"><th colSpan={3} scope="colgroup">{block.text}</th></tr>;
  return <tr key={i} className={block.kind==='role'?'tailoring-role-row':''}>
   <th scope="row">{company||section}</th>
   <td>{editing?<><label>Resume {block.kind}<textarea value={block.text} rows={block.kind==='bullet'?4:2} onChange={e=>change(i,e.target.value)}/></label><button disabled={busy} onClick={()=>setBlocks(old=>old.filter((_,j)=>i!==j))}>Remove</button></>:<p>{block.text}</p>}
   {finding&&!finding.supported&&<p role="alert">Needs correction: {finding.reason}</p>}
   </td>
   <td>{block.kind!=='heading'&&<>{block.reason&&<p className="tailoring-wording-reason">{block.reason}</p>}{block.citations.map((ref,j)=>{const source=record.context.sources.find(s=>s.id===ref.source_id);return <details key={j}><summary>{source?.title||ref.source_id}</summary>{source?.start_line&&<p className="tailoring-source-location">Passage starts at line {source.start_line}</p>}<blockquote>{ref.quote}</blockquote></details>;})}</>}</td>
  </tr>;
 });})()}</tbody></table></div>
 {!running&&<div className="tailoring-actions">{editing?<><button className="primary" disabled={busy||!blocks.length} onClick={()=>void act('/edit',{revision:record.revision,blocks})}>Save edits</button><button onClick={()=>setEditing(false)}>Cancel edits</button><p>Existing citations are preserved. Save, then check edited claims before approving.</p></>:<>
 <button disabled={busy} onClick={()=>{setBlocks(record.draft!.blocks.map(b=>({...b})));setEditing(true);setConfirmed(false);}}>Edit draft</button>
 {['EDITED','NEEDS_REVIEW'].includes(record.state)&&<button disabled={busy} onClick={()=>void act('/check',{revision:record.revision,confirmed:true})}>Check claims · API usage</button>}
 {record.state==='DRAFT'&&<><label className="tailor-checkbox"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> I reviewed the wording, sources, and accuracy of this resume.</label><button className="primary" disabled={busy||!confirmed} onClick={()=>void act('/approve',{revision:record.revision,confirmed:true})}>Approve this resume</button></>}
 {record.state==='APPROVED'&&<><button className="primary" disabled={busy} onClick={()=>void download()}>Download text</button><p>This first export is plain text. It contains only resume content; Word/PDF layout export is not included yet.</p></>}
 </>}</div>}
 </>}
 </>}
 </>}
 </dialog></>;
}
