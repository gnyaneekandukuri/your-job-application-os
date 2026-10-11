import {changedFromMaster} from './resumeChanges';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
const ResumeDocument=lazy(()=>import('./ResumeDocument').then(module=>({default:module.ResumeDocument})));

type Block={change_type?:string;jd_quote?:string;item_id?:string;format?:{font_family:string;font_size:number;bold:boolean;italic:boolean;align:string};kind:'heading'|'contact'|'role'|'bullet'|'text';text:string;citations:{source_id:string;quote:string}[];reason:string};
type Proposal={after_id:string;id:string;operation:string;status:string;error:string;text:string;reason:string;jd_quote:string;before:Block[];citations:Block["citations"]};
type RecordValue={review_warnings?:string[];include_summary?:boolean;job_review?:{summary:string;priorities:{jd_quote:string;status:string;block_indices:number[];reason:string;improvement:string}[];readability:string[];preservation:string[]}|null;quality?:{framework:string;passed:number;total:number;required:number;percent:number|null;meets_threshold:boolean;failures:{block_index:number;reason:string}[]}|null;proposals?:Proposal[];state:string;stage:string;error:string;revision:string;framework:string;policy_version:string;draft:{blocks:Block[];questions:string[]}|null;checks:{findings:{block_index:number;supported:boolean;reason:string}[]}|null;context:{master?:Block[];sources:{id:string;title:string;text:string;employer?:string;start_line?:number}[]}};
type FinalReview={score:number|null;low:number|null;high:number|null;uncertain_count:number;rubric:string;rows:{jd_quote:string;category:string;weight:number;status:string;reason:string;resume_quote:string;points:number|null}[]};
type View={final_review_supported?:boolean;final_review?:FinalReview|null;summary_supported?:boolean;job_url?:string;exported?:{revision:string;created_at:string}|null;quality_checks_version?:number;framework:string;base_resume:string;base_title:string;configured:boolean;record:RecordValue|null;citation_sources?:RecordValue['context']['sources']};
const styles=[['PAR','Recommended · Project, Action, Result'],['XYZ','Metrics-focused · Accomplishment, measurement, method'],['STAR','Context-focused · Concise STAR'],['EXISTING','Keep my existing style']];
const examples:Record<string,string>={PAR:'Show the work, your contribution, and the supported outcome. Include a supported metric in each experience bullet; missing numbers are flagged for your input, never invented.',XYZ:'Lead with a measured accomplishment, then explain how you achieved it. Use only documented metrics.',STAR:'Briefly explain the problem, your action, and the result.',EXISTING:'Preserve your voice and section structure while improving relevance and clarity.'};
export function TailorResume({jobId,label}:{jobId:string;label?:string}) {
 const [confirmApplied,setConfirmApplied]=useState(false),[applicationSaved,setApplicationSaved]=useState(false),[appliedDate,setAppliedDate]=useState(()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;});
 const [proposalFeedback,setProposalFeedback]=useState<{id:string;message:string;error:boolean}|null>(null);
 const dialog=useRef<HTMLDialogElement>(null);
 const finalReviewPanel=useRef<HTMLElement>(null);
 const [open,setOpen]=useState(false),[view,setView]=useState<View|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [framework,setFramework]=useState('PAR'),[base,setBase]=useState(''),[remember,setRemember]=useState(false),[editing,setEditing]=useState(false),[blocks,setBlocks]=useState<Block[]>([]),[confirmed,setConfirmed]=useState(false);
 const [checkingFinal,setCheckingFinal]=useState(false);
 const [includeSummary,setIncludeSummary]=useState(false);
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
 async function checkFinal(){
  if(busy||running||!record)return;
  finalReviewPanel.current?.scrollIntoView({behavior:'smooth',block:'start'});
  if(view?.final_review&&!editing)return;
  setCheckingFinal(true);setBusy(true);setError('');
  try{let current=view!;if(editing){current=await request('/edit',{revision:record.revision,blocks:blocks.filter(b=>b.text.trim())});setView(current);setEditing(false);}
   setView(await request('/final-review',{revision:current.record!.revision,confirmed:true}));}
  catch(e){setError(String(e));}finally{setCheckingFinal(false);setBusy(false);}
 }
 async function recordApplication(){if(!record||busy)return;setBusy(true);setError('');try{const response=await fetch(`/api/jobs/${jobId}/applications`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify({revision:view?.exported?.revision||record.revision,confirmed:true,applied_date:appliedDate})});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not record application.');setApplicationSaved(true);setConfirmApplied(false);}catch(e){setError(String(e));}finally{setBusy(false);}}
 useEffect(()=>{if(!open)return;let live=true;void fetch('/api/applications').then(r=>{if(!r.ok)throw Error('Could not load application status.');return r.json();}).then(rows=>{if(live)setApplicationSaved(rows.some((r:{job_id:string})=>r.job_id===jobId));}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[open,jobId]);
 async function decideProposal(id:string,decision:'accept'|'reject'){
  if(busy||!record)return;setBusy(true);setError('');setProposalFeedback(null);
  try{const next=await request('/proposal',{revision:record.revision,proposal_id:id,decision});setView(next);setEditing(false);setEditorReset(n=>n+1);setProposalFeedback({id,message:decision==='accept'?'Applied and saved in your resume below. Changed or added paragraphs are highlighted yellow.':'Suggestion declined. Your resume is unchanged.',error:false});}
  catch(e){setProposalFeedback({id,message:String(e),error:true});}finally{setBusy(false);}
 }
 function close(){if(editing&&!window.confirm('Discard unsaved resume edits?'))return;setOpen(false);}
 useEffect(()=>{if(!editing)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[editing]);
 const visibleBlocks=editing?blocks:record?.draft?.blocks||[];
 const appliedChanges=visibleBlocks.map((block,index)=>({block,index,original:record?.context.master?.find(item=>item.item_id===block.item_id)})).filter(({block,original})=>changedFromMaster(block,original));
 const selectedBlock=visibleBlocks[selected];
 const selectedFinding=!editing?record?.checks?.findings.find(f=>f.block_index===selected):undefined;
 async function downloadPdf(revision:string){
  const response=await fetch(path+`/pdf?revision=${encodeURIComponent(revision)}`);
  if(!response.ok){const data=await response.json();throw Error(data.detail||'PDF download failed.');}
  const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=decodeURIComponent(response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]||'resume.pdf');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  setView(await request());
 }
 async function finish(){
  if(busy||running||!view?.record)return;setBusy(true);setError('');setConfirmApplied(false);
  try{
   let current=view;
   if(editing){current=await request('/edit',{revision:view.record.revision,blocks:blocks.filter(b=>b.text.trim())});setView(current);setEditing(false);}
   current=await request('/prepare-download',{revision:current.record!.revision,confirmed:true});setView(current);
   await downloadPdf(current.record!.revision);
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 return <><button onClick={()=>{setError('');setEditing(false);setConfirmed(false);setOpen(true);}}>Tailor resume</button>
 <dialog ref={dialog} className="skills-dialog tailoring-dialog" onCancel={e=>{e.preventDefault();close();}} aria-label="Tailor resume">
 <div className="skills-dialog-heading"><div><h2>Tailor your resume</h2><p>{label||"Review the draft, make your edits, then save and download."}</p></div><button onClick={close} aria-label="Close resume tailoring">Close</button></div>
 {error&&<p role="alert">{error}</p>}
 {!view?<p>Loading your resume…</p>:<>
 {record?.draft&&<div className="resume-score-entry"><button className="primary" disabled={busy||running||!view.final_review_supported} onClick={()=>void checkFinal()}>{checkingFinal?'Getting your score…':editing?'Save & get JD match score · API usage':view.final_review?'View JD match score':'Get JD match score · API usage'}</button><span>Optional · compares only your final resume and JD.</span>{!view.final_review_supported&&<span>The server needs updating to enable scoring.</span>}</div>}
 <details open={!record}><summary>Resume and writing approach</summary>
 <label>Writing approach<select value={framework} disabled={busy||running} onChange={e=>setFramework(e.target.value)}>{styles.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label><p>{examples[framework]}</p>
 <label className="tailor-checkbox"><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)}/> Remember this approach for future jobs</label>
 {view.summary_supported&&<label className="tailor-checkbox"><input type="checkbox" checked={includeSummary} disabled={busy||running} onChange={e=>setIncludeSummary(e.target.checked)}/> Propose a professional summary (you choose whether to add it)</label>}
 <label>Base resume<textarea rows={10} maxLength={22000} value={base} disabled={busy||running} onChange={e=>setBase(e.target.value)} placeholder="Paste your resume here, or add a resume in Your context → Add skills."/></label>
 <p>This improves wording in your master and proposes stronger accomplishments from your stories. Replacements, additions and removals need your acceptance. It uses your JD, relevant saved evidence, and the resume skill. Drafting and claim checking use OpenAI; costs appear in API costs. Your master resume stays unchanged.</p>
 <button className="primary" disabled={busy||running||editing||!view.configured||!view.quality_checks_version||!base.trim()} onClick={()=>void act('',{consent_to_send:true,framework,base_resume:base,remember,retry:false,...(view.summary_supported?{include_summary:includeSummary}:{})})}>Prepare draft · API usage</button>
 {!view.configured&&<p role="alert">Connect your OpenAI API key to prepare a draft.</p>}
 </details>
 <details><summary>Ground rules included</summary><p>PAR by default, with XYZ, concise STAR, or your existing style. Every claim needs candidate evidence. Preserve ownership, dates, titles, numbers, and project context. Never invent qualifications or copy requirements as experience. Use plain language, relevant accomplishments, and readable formatting. Review changes before approval; no automatic applications.</p><p>Citation checks and an AI claim review help catch errors; they are not independent verification of your experience.</p></details>
 {record&&<><p role="status"><strong>{record.state==='APPROVED'?'Approved resume':['ERROR','INTERRUPTED'].includes(record.state)?'Preparation stopped — draft saved':record.stage||record.state}</strong> · {record.framework} · Rules v{record.policy_version}</p>
 {running&&<p>Preparation continues on the server if you close this window or refresh.</p>}
 {record.error&&<p role="alert">{record.error}</p>}
 {['ERROR','INTERRUPTED'].includes(record.state)&&!record.draft&&!editing&&<button disabled={busy} onClick={()=>void act('',{consent_to_send:true,framework:record.framework,base_resume:record.context.sources[0].text,retry:true,...(view.summary_supported?{include_summary:record.include_summary??false}:{})})}>Retry unfinished preparation · API usage</button>}
 {record.draft&&<>
 {record.job_review&&!editing&&<section className="resume-quality" aria-label="Resume against this JD"><h3>How this draft addresses the job</h3><p>{record.job_review.summary}</p>{record.job_review.priorities.map((p,i)=><details key={i}><summary>{p.status==='clear'?'Clearly demonstrated':p.status==='weak'?'Could be stronger':'Not demonstrated'} · {p.jd_quote}</summary><p>{p.reason}</p>{p.block_indices.map(index=><blockquote key={index}>{record.draft?.blocks[index]?.text}</blockquote>)}{p.improvement&&<p><strong>Suggested improvement:</strong> {p.improvement}</p>}</details>)}{record.job_review.readability.length>0&&<><h4>Clarity and emphasis</h4><ul>{record.job_review.readability.map((x,i)=><li key={i}>{x}</li>)}</ul></>}{record.job_review.preservation.length>0&&<><h4>Preserving your background</h4><ul>{record.job_review.preservation.map((x,i)=><li key={i}>{x}</li>)}</ul></>}<p>Review of the AI draft. Suggestions do not block your edits or download.</p></section>}
 <section aria-label="Resume quality checks" className="resume-quality">
 <h3>Experience bullet quality</h3>
 {record.review_warnings?.length? <details><summary>Some statements could not be checked</summary><p>Your draft is saved. This is an incomplete review, not a rejection of your experience.</p><ul>{record.review_warnings.map((w,i)=><li key={i}>{w}</li>)}</ul></details>:null}
 {!view.quality_checks_version&&<p>The stronger checks need the server update to finish. Existing scan work is being preserved.</p>}
 {record.quality?<><p><strong>{record.quality.passed} of {record.quality.total} bullets meet {record.quality.framework}</strong> · {record.quality.required} required (80%). {record.quality.meets_threshold?'Coverage requirement met.':'Suggestions for the AI draft; you can edit and download your version.'}</p>
 {record.quality.failures.length>0&&<details><summary>Bullets needing work ({record.quality.failures.length})</summary>{record.quality.failures.map(f=><div key={f.block_index}><p><strong>{record.draft?.blocks[f.block_index]?.text}</strong></p><p>{f.reason}</p></div>)}</details>}</>:<p>Your edits are saved as your own resume wording.</p>}
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
 <p><strong>Reason category:</strong> {block.change_type==='fact_correction'?'Factual correction':block.change_type==='framework'?'Writing framework':block.change_type==='job_alignment'?'Job requirement':'Not recorded in this older draft'}</p>
 {block.jd_quote?<blockquote><strong>JD requirement:</strong> {block.jd_quote}</blockquote>:<p>{['fact_correction','framework'].includes(block.change_type||'')?'This change does not claim a specific JD requirement.':'No exact JD quotation was saved for this edit.'}</p>}
 <button onClick={()=>{setSelected(index);setShowEvidence(true);}}>Show evidence for this change</button>
 </details>)}
 </section>
 <section className="resume-proposals" aria-label="Tailoring suggestions">
 {record.proposals&&<h3>Suggestions not yet applied ({record.proposals.filter(p=>p.status==='pending').length})</h3>}
 {record.proposals?.length===0&&<p>No structural changes proposed. Wording changes appear in yellow below.</p>}
 {record.proposals?.map(p=><details key={p.id}><summary>{p.operation==='summary'?'Add professional summary':p.operation.charAt(0).toUpperCase()+p.operation.slice(1)+' a resume point'} · {p.status==='pending'?'Not in the draft':p.status==='accepted'?'Applied to draft':'Declined'}</summary>
 <p><strong>Why for this job:</strong> {p.reason}</p><p><strong>Job requirement:</strong> {p.jd_quote}</p>
 <div className="resume-proposal-comparison pending-comparison"><div><h4>Before this suggestion</h4>{p.before.length?p.before.map(b=><p key={b.item_id}>{b.text}</p>):<p>New point</p>}</div><div><h4>{p.status==='accepted'?'Accepted wording':'Proposed wording — not applied'}</h4><p>{p.text|| (p.operation==='remove'?'Remove this point.':'Move this point to the proposed position.')}</p></div></div>
 {p.after_id&&<p><strong>Place after:</strong> {record.draft?.blocks.find(b=>b.item_id===p.after_id)?.text||p.after_id}</p>}
 <details><summary>Source evidence</summary>{p.citations.map((ref,i)=><div key={i}><strong>{view.citation_sources?.find(s=>s.id===ref.source_id)?.title||ref.source_id}</strong><blockquote>{ref.quote}</blockquote></div>)}</details>
 {p.error&&<p role="alert">{p.error}</p>}
 {proposalFeedback?.id===p.id&&<p role={proposalFeedback.error?"alert":"status"}>{proposalFeedback.message}</p>}
 {editing&&p.status==='pending'&&<p>Save your current resume edits first to apply this suggestion.</p>}
 {p.status==='pending'&&<div className="resume-proposal-actions"><button disabled={busy||running||editing||!!p.error} onClick={()=>void decideProposal(p.id,'accept')}>Apply this exact change</button><button disabled={busy||running||editing} onClick={()=>void decideProposal(p.id,'reject')}>Keep current resume</button></div>}
 </details>)}
 </section>
 {view.final_review_supported&&<section ref={finalReviewPanel} className="resume-quality" aria-label="Final resume against JD" style={{scrollMarginTop:120}}>
 <h3>Final JD match score</h3>
 <p>Uses only the saved resume and JD. Save your edits before checking.</p>
 {checkingFinal&&<p role="status">Comparing the saved resume with the job requirements…</p>}
 {editing&&<p>Save changes to check this version. Any score below is for the previous saved version.</p>}
 {view.final_review&&<><p><strong>{view.final_review.score!==null?`Resume coverage: ${view.final_review.score}/100`:view.final_review.low!==null?`Provisional coverage: ${view.final_review.low}–${view.final_review.high}/100 · needs review`:'Not enough clear JD requirements to score'}</strong></p>
 {view.final_review.uncertain_count>0&&<p>{view.final_review.uncertain_count} criteria need review; uncertainty is not a rejection.</p>}
 <details><summary>How the score works</summary><p>{view.final_review.rubric}</p></details>
 {view.final_review.rows.map((row,i)=><details key={i}><summary>{row.status==='clear'?'Clearly demonstrated':row.status==='partial'?'Partially demonstrated':row.status==='missing'?'Not demonstrated':'Needs review'} · {row.jd_quote}</summary><p>{row.reason} {row.weight?`${row.points===null?'Unresolved':row.points} / ${row.weight} weighted points.`:'Ambiguous requirement; not scored.'}</p>{row.resume_quote&&<blockquote>{row.resume_quote}</blockquote>}</details>)}
 </>}
 </section>}
 <div className="resume-action-toolbar">
 <strong>{editing?'Unsaved edits':record.state==='APPROVED'?'Approved resume':'Your tailored draft'}</strong>
 <button className="primary" disabled={busy||running||!editing||!blocks.some(b=>b.text.trim())} onClick={()=>void act('/edit',{revision:record.revision,blocks:blocks.filter(b=>b.text.trim())})}>Save changes</button>
 {editing&&<button disabled={busy} onClick={()=>{setEditing(false);setEditorReset(n=>n+1);setSelected(0);}}>Discard edits</button>}
 {view.final_review_supported&&<button disabled={busy||running} title={editing?'Save your edits and check this version':'Optional Jev check using only your saved resume and JD'} onClick={()=>void checkFinal()}>{checkingFinal?'Checking JD match…':editing?'Save & get JD match score · API usage':view.final_review?'View JD match score':'Get JD match score · API usage'}</button>}

 <button className="primary" disabled={busy||running} onClick={()=>void finish()}>Done — save &amp; download PDF</button>
 <span className="resume-export-note">Saves your edits and downloads this version. No API usage.</span>
 <button aria-pressed={showEvidence} onClick={()=>setShowEvidence(v=>!v)}>{showEvidence?'Hide evidence':'Show evidence'}</button>
 </div>
 {view.exported&&!editing&&<section className="application-confirmation" aria-label="Apply with saved resume"><h3>Your saved PDF</h3><p>Version {view.exported.revision.slice(0,8)} · {new Date(view.exported.created_at).toLocaleString()}{view.exported.revision!==record.revision?' · Earlier downloaded version; finish the current draft to create a new PDF.':''}</p><button disabled={busy||running} onClick={()=>{setBusy(true);setError('');void downloadPdf(view.exported!.revision).catch(e=>setError(String(e))).finally(()=>setBusy(false));}}>Download saved PDF again</button> {view.job_url&&/^https?:\/\//.test(view.job_url)?<a className="primary" href={view.job_url} target="_blank" rel="noreferrer">Open job &amp; apply ↗</a>:<p>No application link was saved for this role. Use the original job listing.</p>} <button disabled={busy||running||applicationSaved} onClick={()=>setConfirmApplied(true)}>{applicationSaved?'Recorded in Applied jobs':'I applied'}</button></section>}
 {confirmApplied&&view.exported&&!editing&&<form className="application-confirmation" onSubmit={e=>{e.preventDefault();void recordApplication();}}><h3>Record your application</h3><p>Confirm you submitted this application using saved PDF version {view.exported.revision.slice(0,8)}. This exact copy will be kept in Applied jobs.</p><label>Application date <input type="date" required value={appliedDate} onChange={e=>setAppliedDate(e.target.value)}/></label><button type="button" disabled={busy} onClick={()=>setConfirmApplied(false)}>Cancel</button> <button className="primary" disabled={busy||!appliedDate}>Confirm I applied</button></form>}
 <p className="resume-change-legend"><span aria-hidden="true"/> Yellow means this paragraph contains edits or is new. Unchanged words within an edited paragraph are highlighted too. Select one to compare the original and evidence.</p>
 <div className={`resume-workspace ${showEvidence?'with-evidence':''}`}>
 <Suspense fallback={<p>Opening document editor…</p>}><ResumeDocument key={`${record.revision}:${editorReset}`} initial={record.draft.blocks} master={record.context.master} disabled={busy||!!running} onChange={next=>{setBlocks(next);setEditing(JSON.stringify(next)!==JSON.stringify(record.draft!.blocks));setConfirmed(false);}} onSelect={setSelected}/></Suspense>
 {showEvidence&&<aside className="resume-evidence-panel" aria-label="Selected point evidence">
 <h3>Evidence</h3>{selectedBlock&&record.context.master&&<strong className="resume-change-status">{changedFromMaster(selectedBlock,record.context.master.find(b=>b.item_id===selectedBlock.item_id))?'Changed from master':'Unchanged from master'}</strong>}<p className="resume-panel-help">Select a point in the resume to see its sources.</p>
 {selectedBlock&&<><p className="resume-selected-point">{selectedBlock.text}</p>
 {editing&&<p>Your edits will be included in the saved PDF.</p>}
 {selectedFinding&&!selectedFinding.supported&&<p role="alert">{selectedFinding.reason}</p>}
 {selectedBlock.reason&&<><h4>Why this wording</h4><p>{selectedBlock.reason}</p>{selectedBlock.jd_quote&&<blockquote><strong>JD requirement:</strong> {selectedBlock.jd_quote}</blockquote>}</>}
 {record.context.master&&selectedBlock.item_id&&<details><summary>Original master item</summary><p>{record.context.master.find(b=>b.item_id===selectedBlock.item_id)?.text}</p></details>}
 <h4>Sources</h4>{selectedBlock.citations.length===0?<p>{selectedBlock.kind==='heading'?'Section headings do not need evidence.':'No source attached. Add factual details to Your context and prepare a new draft, or use existing supported wording.'}</p>:selectedBlock.citations.map((ref,j)=>{const source=(view.citation_sources||record.context.sources).find(s=>s.id===ref.source_id);return <div className="resume-source" key={j}><strong>{source?.title||ref.source_id}</strong>{source?.start_line&&<p>Passage starts at line {source.start_line}</p>}<blockquote>{ref.quote}</blockquote></div>;})}
 </>}
 </aside>}
 </div>
 <p className="resume-export-note">PDFs contain the saved resume text and formatting, without yellow highlights or evidence notes. Downloading or opening the job does not mark it as applied.</p>
 </>}
 </>}
 </>}
 </dialog></>;
}
