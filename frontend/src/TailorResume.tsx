import {changedFromMaster} from './resumeChanges';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
const ResumeDocument=lazy(()=>import('./ResumeDocument').then(module=>({default:module.ResumeDocument})));

type Block={item_id?:string;format?:{font_family:string;font_size:number;bold:boolean;italic:boolean;align:string};kind:'heading'|'contact'|'role'|'bullet'|'text';text:string;citations:{source_id:string;quote:string}[];reason:string};
type Proposal={after_id:string;id:string;operation:string;status:string;error:string;text:string;reason:string;jd_quote:string;before:Block[];citations:Block["citations"]};
type RecordValue={quality?:{framework:string;passed:number;total:number;required:number;percent:number|null;meets_threshold:boolean;failures:{block_index:number;reason:string}[]}|null;proposals?:Proposal[];state:string;stage:string;error:string;revision:string;framework:string;policy_version:string;draft:{blocks:Block[];questions:string[]}|null;checks:{findings:{block_index:number;supported:boolean;reason:string}[]}|null;context:{master?:Block[];sources:{id:string;title:string;text:string;employer?:string;start_line?:number}[]}};
type View={quality_checks_version?:number;framework:string;base_resume:string;base_title:string;configured:boolean;record:RecordValue|null;citation_sources?:RecordValue['context']['sources']};
const styles=[['PAR','Recommended · Project, Action, Result'],['XYZ','Metrics-focused · Accomplishment, measurement, method'],['STAR','Context-focused · Concise STAR'],['EXISTING','Keep my existing style']];
const examples:Record<string,string>={PAR:'Show the work, your contribution, and the supported outcome. Numbers are optional.',XYZ:'Lead with a measured accomplishment, then explain how you achieved it. Use only documented metrics.',STAR:'Briefly explain the problem, your action, and the result.',EXISTING:'Preserve your voice and section structure while improving relevance and clarity.'};
export function TailorResume({jobId,label}:{jobId:string;label?:string}) {
 const dialog=useRef<HTMLDialogElement>(null);
 const [open,setOpen]=useState(false),[view,setView]=useState<View|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [framework,setFramework]=useState('PAR'),[base,setBase]=useState(''),[remember,setRemember]=useState(false),[editing,setEditing]=useState(false),[blocks,setBlocks]=useState<Block[]>([]),[confirmed,setConfirmed]=useState(false);
 const [selected,setSelected]=useState(0),[showEvidence,setShowEvidence]=useState(true),[editorReset,setEditorReset]=useState(0);
 const path=`/api/jobs/${jobId}/tailoring`,record=view?.record,running=record?.state==='RUNNING';
 async function request(suffix='',body?:unknown):Promise<View>{
  const response=await fetch(path+suffix,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Could not save this resume. Check the fields and try again.');return data;
 }
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();},[open]);
 useEffect(()=>{if(!open)return;let live=true;void request().then(data=>{if(live){setView(data);setFramework(data.record?.framework||data.framework);setBase(data.record?.context.sources[0]?.text||data.base_resume);}}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[open,jobId]);
 useEffect(()=>{if(!open||!running)return;let live=true;const timer=setInterval(()=>{void request().then(data=>{if(live)setView(data);}).catch(e=>{if(live)setError(String(e));});},2500);return()=>{live=false;clearInterval(timer);};},[open,running,jobId]);
 async function act(suffix:string,body:unknown){if(busy)return;setBusy(true);setError('');setConfirmed(false);try{setView(await request(suffix,body));setEditing(false);}catch(e){setError(String(e));}finally{setBusy(false);}}
 function close(){if(editing&&!window.confirm('Discard unsaved resume edits?'))return;setOpen(false);}
 useEffect(()=>{if(!editing)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[editing]);
 const visibleBlocks=editing?blocks:record?.draft?.blocks||[];
 const appliedChanges=visibleBlocks.map((block,index)=>({block,index,original:record?.context.master?.find(item=>item.item_id===block.item_id)})).filter(({block,original})=>changedFromMaster(block,original));
 const selectedBlock=visibleBlocks[selected];
 const selectedFinding=!editing?record?.checks?.findings.find(f=>f.block_index===selected):undefined;
 async function download(){setBusy(true);setError('');try{const response=await fetch(path+`/export?revision=${encodeURIComponent(record!.revision)}`);if(!response.ok){const data=await response.json();throw new Error(data.detail||'Export failed.');}const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='tailored-resume.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <><button onClick={()=>{setError('');setEditing(false);setConfirmed(false);setOpen(true);}}>Tailor resume</button>
 <dialog ref={dialog} className="skills-dialog tailoring-dialog" onCancel={e=>{e.preventDefault();close();}} aria-label="Tailor resume">
 <div className="skills-dialog-heading"><div><h2>Tailor your resume</h2><p>{label||"Choose an approach, review the evidence, then approve your draft."}</p></div><button onClick={close} aria-label="Close resume tailoring">Close</button></div>
 {error&&<p role="alert">{error}</p>}
 {!view?<p>Loading your resume…</p>:<>
 <details open={!record}><summary>Resume and writing approach</summary>
 <label>Writing approach<select value={framework} disabled={busy||running} onChange={e=>setFramework(e.target.value)}>{styles.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label><p>{examples[framework]}</p>
 <label className="tailor-checkbox"><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)}/> Remember this approach for future jobs</label>
 <label>Base resume<textarea rows={10} maxLength={22000} value={base} disabled={busy||running} onChange={e=>setBase(e.target.value)} placeholder="Paste your resume here, or add a resume in Your context → Add skills."/></label>
 <p>This improves wording in your master and proposes stronger accomplishments from your stories. Replacements, additions and removals need your acceptance. It uses your JD, relevant saved evidence, and the resume skill. Drafting and claim checking use OpenAI; costs appear in API costs. Your master resume stays unchanged.</p>
 <button className="primary" disabled={busy||running||editing||!view.configured||!view.quality_checks_version||!base.trim()} onClick={()=>void act('',{consent_to_send:true,framework,base_resume:base,remember,retry:false})}>Prepare draft · API usage</button>
 {!view.configured&&<p role="alert">Connect your OpenAI API key to prepare a draft.</p>}
 </details>
 <details><summary>Ground rules included</summary><p>PAR by default, with XYZ, concise STAR, or your existing style. Every claim needs candidate evidence. Preserve ownership, dates, titles, numbers, and project context. Never invent qualifications or copy requirements as experience. Use plain language, relevant accomplishments, and readable formatting. Review changes before approval; no automatic applications.</p><p>Citation checks and an AI claim review help catch errors; they are not independent verification of your experience.</p></details>
 {record&&<><p role="status"><strong>{record.state==='APPROVED'?'Approved resume':['ERROR','INTERRUPTED'].includes(record.state)?'Preparation stopped — draft saved':record.stage||record.state}</strong> · {record.framework} · Rules v{record.policy_version}</p>
 {running&&<p>Preparation continues on the server if you close this window or refresh.</p>}
 {record.error&&<p role="alert">{record.error}</p>}
 {['ERROR','INTERRUPTED'].includes(record.state)&&!record.draft&&!editing&&<button disabled={busy} onClick={()=>void act('',{consent_to_send:true,framework:record.framework,base_resume:record.context.sources[0].text,retry:true})}>Retry unfinished preparation · API usage</button>}
 {record.draft&&<>
 <section aria-label="Resume quality checks" className="resume-quality">
 <h3>Experience bullet quality</h3>
 {!view.quality_checks_version&&<p>The stronger checks need the server update to finish. Existing scan work is being preserved.</p>}
 {record.quality?<><p><strong>{record.quality.passed} of {record.quality.total} bullets meet {record.quality.framework}</strong> · {record.quality.required} required (80%). {record.quality.meets_threshold?'Coverage requirement met.':'Improve the flagged bullets before approval.'}</p>
 {record.quality.failures.length>0&&<details><summary>Bullets needing work ({record.quality.failures.length})</summary>{record.quality.failures.map(f=><div key={f.block_index}><p><strong>{record.draft?.blocks[f.block_index]?.text}</strong></p><p>{f.reason}</p></div>)}</details>}</>:<p>Framework coverage and impact meaning have not been checked for this version.</p>}
 {!running&&!editing&&!record.quality&&!!view.quality_checks_version&&<button disabled={busy} onClick={()=>void act('/check',{revision:record.revision,confirmed:true})}>Run stronger checks · API usage</button>}
 {record.checks?.findings.some(f=>!f.supported)&&<details><summary>Impact or evidence issues ({record.checks.findings.filter(f=>!f.supported).length})</summary>{record.checks.findings.filter(f=>!f.supported).map(f=><div key={f.block_index}><p><strong>{record.draft?.blocks[f.block_index]?.text}</strong></p><p>{f.reason}</p></div>)}</details>}
 </section>
 {!record.proposals&&<p>This saved draft predates job-specific structural suggestions. <button disabled={busy||running||editing} onClick={()=>void act('',{consent_to_send:true,framework:record.framework,base_resume:record.context.sources[0].text,remember:false,retry:false})}>Prepare updated tailoring · API usage</button></p>}
 {record.draft.questions.length>0&&<details><summary>Questions and missing details ({record.draft.questions.length})</summary><ul>{record.draft.questions.map((q,i)=><li key={i}>{q}</li>)}</ul><p>Add factual details to the relevant experience in Your context, then reassess before generating a new draft.</p></details>}
 <section className="resume-proposals" aria-label="Changes already in the draft">
 <h3>Changes already in the draft ({appliedChanges.length})</h3>
 <p>These exact changes are visible in yellow in the resume. Pending suggestions below have not been added.</p>
 {appliedChanges.map(({block,index,original})=><details key={`${block.item_id}:${index}`}><summary>{block.kind==='role'?'Role details':block.kind==='bullet'?'Resume point':'Resume text'} · {block.text.slice(0,85)}{block.text.length>85?'…':''}</summary>
 <div className="resume-proposal-comparison"><div><h4>Master resume</h4><p>{original?.text||'New point'}</p></div><div><h4>In your draft now</h4><p>{block.text}</p></div></div>
 <p><strong>Why:</strong> {block.reason}</p>
 <button onClick={()=>{setSelected(index);setShowEvidence(true);}}>Show evidence for this change</button>
 </details>)}
 </section>
 <section className="resume-proposals" aria-label="Tailoring suggestions">
 {record.proposals&&<h3>Suggestions not yet applied ({record.proposals.filter(p=>p.status==='pending').length})</h3>}
 {record.proposals?.length===0&&<p>No structural changes proposed. Wording changes appear in yellow below.</p>}
 {record.proposals?.map(p=><details key={p.id}><summary>{p.operation.charAt(0).toUpperCase()+p.operation.slice(1)} a resume point · {p.status==='pending'?'Not in the draft':p.status==='accepted'?'Applied to draft':'Declined'}</summary>
 <p><strong>Why for this job:</strong> {p.reason}</p><p><strong>Job requirement:</strong> {p.jd_quote}</p>
 <div className="resume-proposal-comparison pending-comparison"><div><h4>Before this suggestion</h4>{p.before.length?p.before.map(b=><p key={b.item_id}>{b.text}</p>):<p>New point</p>}</div><div><h4>{p.status==='accepted'?'Accepted wording':'Proposed wording — not applied'}</h4><p>{p.text|| (p.operation==='remove'?'Remove this point.':'Move this point to the proposed position.')}</p></div></div>
 {p.after_id&&<p><strong>Place after:</strong> {record.draft?.blocks.find(b=>b.item_id===p.after_id)?.text||p.after_id}</p>}
 <details><summary>Source evidence</summary>{p.citations.map((ref,i)=><div key={i}><strong>{view.citation_sources?.find(s=>s.id===ref.source_id)?.title||ref.source_id}</strong><blockquote>{ref.quote}</blockquote></div>)}</details>
 {p.error&&<p role="alert">{p.error}</p>}
 {p.status==='pending'&&<div className="resume-proposal-actions"><button disabled={busy||running||editing||!!p.error} onClick={()=>void act('/proposal',{revision:record.revision,proposal_id:p.id,decision:'accept'})}>Apply this exact change</button><button disabled={busy||running||editing} onClick={()=>void act('/proposal',{revision:record.revision,proposal_id:p.id,decision:'reject'})}>Keep current resume</button></div>}
 </details>)}
 </section>
 <div className="resume-action-toolbar">
 <strong>{editing?'Unsaved edits':record.state==='APPROVED'?'Approved resume':'Your tailored draft'}</strong>
 <button className="primary" disabled={busy||running||!editing||!blocks.some(b=>b.text.trim())} onClick={()=>void act('/edit',{revision:record.revision,blocks:blocks.filter(b=>b.text.trim())})}>Save changes</button>
 {editing&&<button disabled={busy} onClick={()=>{setEditing(false);setEditorReset(n=>n+1);setSelected(0);}}>Discard edits</button>}
 {!editing&&['EDITED','NEEDS_REVIEW','ERROR','INTERRUPTED'].includes(record.state)&&<button disabled={busy||running||!view.quality_checks_version} onClick={()=>void act('/check',{revision:record.revision,confirmed:true})}>Check changes · API usage</button>}
 {!editing&&record.state==='DRAFT'&&<><label className="tailor-checkbox"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> I reviewed this resume</label><button className="primary" disabled={busy||!confirmed||!record.quality?.meets_threshold||!view.quality_checks_version||record.proposals?.some(p=>p.status==='pending')} onClick={()=>void act('/approve',{revision:record.revision,confirmed:true})}>Approve</button></>}
 {!editing&&record.state==='APPROVED'&&<button disabled={busy} onClick={()=>void download()}>Download text</button>}
 <button aria-pressed={showEvidence} onClick={()=>setShowEvidence(v=>!v)}>{showEvidence?'Hide evidence':'Show evidence'}</button>
 </div>
 <p className="resume-change-legend"><span aria-hidden="true"/> Yellow means this paragraph contains edits or is new. Unchanged words within an edited paragraph are highlighted too. Select one to compare the original and evidence.</p>
 <div className={`resume-workspace ${showEvidence?'with-evidence':''}`}>
 <Suspense fallback={<p>Opening document editor…</p>}><ResumeDocument key={`${record.revision}:${editorReset}`} initial={record.draft.blocks} master={record.context.master} disabled={busy||!!running} onChange={next=>{setBlocks(next);setEditing(JSON.stringify(next)!==JSON.stringify(record.draft!.blocks));setConfirmed(false);}} onSelect={setSelected}/></Suspense>
 {showEvidence&&<aside className="resume-evidence-panel" aria-label="Selected point evidence">
 <h3>Evidence</h3>{selectedBlock&&record.context.master&&<strong className="resume-change-status">{changedFromMaster(selectedBlock,record.context.master.find(b=>b.item_id===selectedBlock.item_id))?'Changed from master':'Unchanged from master'}</strong>}<p className="resume-panel-help">Select a point in the resume to see its sources.</p>
 {selectedBlock&&<><p className="resume-selected-point">{selectedBlock.text}</p>
 {editing&&<p>Edits need a new claim check after saving.</p>}
 {selectedFinding&&!selectedFinding.supported&&<p role="alert">{selectedFinding.reason}</p>}
 {selectedBlock.reason&&<><h4>Why this wording</h4><p>{selectedBlock.reason}</p></>}
 {record.context.master&&selectedBlock.item_id&&<details><summary>Original master item</summary><p>{record.context.master.find(b=>b.item_id===selectedBlock.item_id)?.text}</p></details>}
 <h4>Sources</h4>{selectedBlock.citations.length===0?<p>{selectedBlock.kind==='heading'?'Section headings do not need evidence.':'No source attached. Add factual details to Your context and prepare a new draft, or use existing supported wording.'}</p>:selectedBlock.citations.map((ref,j)=>{const source=(view.citation_sources||record.context.sources).find(s=>s.id===ref.source_id);return <div className="resume-source" key={j}><strong>{source?.title||ref.source_id}</strong>{source?.start_line&&<p>Passage starts at line {source.start_line}</p>}<blockquote>{ref.quote}</blockquote></div>;})}
 </>}
 </aside>}
 </div>
 <p className="resume-export-note">Changes are saved locally. Download currently exports plain text; Word/PDF export is not included.</p>
 </>}
 </>}
 </>}
 </dialog></>;
}
