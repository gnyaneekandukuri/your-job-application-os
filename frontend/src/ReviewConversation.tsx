import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type Requirement = {id: string; interpretation: string};
type Source = {id: string; title: string; employer: string; project: string; path: string; revision: string; category: string};
type Proposal = {mode: 'new'|'existing'; input_version: string; source_revisions: Record<string,string>;
  additions: {message_id: string; text: string; source_id: string; requirement_ids: string[]; reason: string}[]};
type CommitBody = {request_id: string; revision: number; consent_to_send: true; destinations: {addition_index: number; source_id: string; expected_revision: string}[]};
type Conversation = {revision: number; messages: {id:string; role:string; text:string}[]; proposal: Proposal|null;
  operation: null|{id:string; state:string; message:string; request:CommitBody}};
export function ReviewConversation({jobId,inputVersion,requirements,focus,onChanged,onClose,onOpen}: {
  jobId:string;inputVersion:string;requirements:Requirement[];focus:{id:string;nonce:number;question?:string}|null;onChanged:()=>void;onClose:()=>void;onOpen:()=>void;
}) {
  const [chat,setChat]=useState<Conversation|null>(null);
  const [sources,setSources]=useState<Source[]>([]);
  const [mode,setMode]=useState<'new'|'existing'>('new');
  const [selected,setSelected]=useState<string[]>([]);
  const [text,setText]=useState('');
  const [destinations,setDestinations]=useState<Record<number,string>>({});
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [loadError,setLoadError]=useState('');
  const [consent,setConsent]=useState(false);
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{if(focus)dialog.current?.showModal();else dialog.current?.close();},[focus]);
  const composer=useRef<HTMLTextAreaElement>(null);
  const inFlight=useRef(false);
  const pendingSend=useRef<Record<string,unknown>|null>(null);
  const pendingCommit=useRef<CommitBody|null>(null);
  const latestRevision=useRef(-1);
  const draftKey=`review-draft:${jobId}`;
  useEffect(()=>{
    try {const draft=JSON.parse(localStorage.getItem(draftKey)||'null');if(draft){setText(draft.text||'');setSelected(draft.selected||[]);setMode(draft.mode||'new');}} catch { /* malformed draft */ }
  },[draftKey]);
  useEffect(()=>{localStorage.setItem(draftKey,JSON.stringify({text,selected,mode}));},[draftKey,text,selected,mode]);
  useEffect(()=>{if(focus){if(focus.id)setSelected(prev=>prev.includes(focus.id)?prev:[...prev,focus.id]);composer.current?.focus({preventScroll:true});}},[focus]);
  function receive(data:Conversation){
    setChat(data);
    if(data.revision!==latestRevision.current){latestRevision.current=data.revision;setDestinations(Object.fromEntries((data.proposal?.additions||[]).map((a,i)=>[i,a.source_id])));}
  }
  useEffect(()=>{
    let cancelled=false;let timer:ReturnType<typeof setTimeout>;
    async function poll(){
      try{
        const [r,s]=await Promise.all([fetch(`/api/jobs/${jobId}/review`),fetch('/api/library')]);
        if(!r.ok||!s.ok)throw new Error('Could not load review conversation.');
        const [data,library]=await Promise.all([r.json(),s.json()]);
        if(!cancelled&&!inFlight.current){receive(data);setLoadError('');setSources(library.sources.filter((v:Source)=>v.category==='story'));}
      }catch(e){if(!cancelled)setLoadError(String(e));}
      finally{if(!cancelled)timer=setTimeout(poll,3000);}
    }
    void poll();return()=>{cancelled=true;clearTimeout(timer);};
  },[jobId]);
  async function post(path:string,body:unknown){
    const r=await fetch(`/api/jobs/${jobId}/review/${path}`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Review could not complete.');return data;
  }
  async function send(){
    if(!chat||inFlight.current)return;inFlight.current=true;setBusy(true);setError('');
    pendingSend.current??={request_id:crypto.randomUUID(),revision:chat.revision,input_version:inputVersion,mode,text:text.trim(),requirement_ids:selected,consent_to_send:true};
    try{receive(await post('messages',pendingSend.current));setText('');pendingSend.current=null;pendingCommit.current=null;}
    catch(e){setError(String(e));pendingSend.current=null;}
    finally{inFlight.current=false;setBusy(false);}
  }
  async function commit(retry=false){
    if(!chat||inFlight.current)return;inFlight.current=true;setBusy(true);setError('');
    const body=retry?chat.operation!.request:(pendingCommit.current??={request_id:crypto.randomUUID(),revision:chat.revision,consent_to_send:true,
      destinations:(chat.proposal?.additions||[]).map((_,i)=>({addition_index:i,source_id:destinations[i],expected_revision:chat.proposal!.source_revisions[destinations[i]]}))});
    try{receive(await post('commit',body));onChanged();pendingCommit.current=null;}
    catch(e){setError(String(e));}
    finally{inFlight.current=false;setBusy(false);}
  }
  const op=chat?.operation;
  const updating=!!op&&['UPDATING','SAVING'].includes(op.state);
  const blocked=updating||op?.state==='PENDING';
  const proposal=chat?.proposal;
  const canSave=proposal&&(proposal.mode==='existing'||(proposal.additions.length>0&&proposal.additions.every((_,i)=>!!destinations[i])));
  return <>{blocked && !focus && <p role="status">{op?.message} <button onClick={onOpen}>View review update</button></p>}{createPortal(<dialog ref={dialog} className="experience-dialog shared-review-dialog" aria-label="Review your analysis" onCancel={onClose}>
    <button className="review-close" onClick={onClose}>Close review</button>
    <section className="review-conversation" aria-label="Shared evidence review">
    <h3>What should we revisit?</h3>
    {focus?.question&&<aside className="review-focus-question"><strong>Question you are reviewing</strong><p>{focus.question}</p>{focus.id&&<small>{requirements.find(r=>r.id===focus.id)?.interpretation}</small>}</aside>}
    <p>Describe missing context or an assessment you disagree with. One experience can address several requirements.</p>
    <div className="review-messages" role="log" aria-label="Review conversation history">
      {!chat?.messages.length&&<p className="review-assistant">Tell me what is missing or what you disagree with. I will propose where new details belong, or help recheck the evidence already saved.</p>}
      {chat?.messages.map(m=><article key={m.id} className={`review-${m.role}`}><strong>{m.role==='user'?'You':'Review assistant'}</strong><p>{m.text}</p></article>)}
    </div>
    {op&&<div role="status" className="review-status"><p>{op.message}</p>{op.state==='PENDING'&&<button disabled={busy} onClick={()=>void commit(true)}>Retry saved review update</button>}</div>}
    {proposal&&!blocked&&op?.state!=='COMPLETE'&&<section className="review-proposal" aria-label="Proposed story additions">
      <h4>{proposal.mode==='existing'?'Recheck existing evidence':'Review proposed additions before saving'}</h4>
      {proposal.mode==='existing'?<p>No story files will change. Your feedback guides a fresh check against original sources; it does not count as evidence.</p>:
        proposal.additions.length===0?<p>No file changes proposed yet. Answer the question above to clarify the experience and employer/project.</p>:
        proposal.additions.map((a,i)=><article key={i}>
          <label>Save addition {i+1} to<select value={destinations[i]||''} onChange={e=>{setDestinations({...destinations,[i]:e.target.value});pendingCommit.current=null;}}>
            <option value="">Choose employer / project story</option>{sources.map(s=><option key={s.id} value={s.id}>{s.title} — {s.employer} / {s.project}</option>)}
          </select></label>
          {destinations[i]&&<small>{sources.find(s=>s.id===destinations[i])?.path}</small>}
          <blockquote>{a.text}</blockquote><p>{a.reason}</p>
          <details><summary>Requirements this may address ({a.requirement_ids.length})</summary><ul>{a.requirement_ids.map(id=><li key={id}>{requirements.find(r=>r.id===id)?.interpretation||id}</li>)}</ul></details>
        </article>)}
      {canSave&&<><p>Saving preserves your exact account, updates embeddings with OpenAI, and reassesses with Jev. The score may stay unchanged; usage is tracked.</p>
        <button className="primary" disabled={busy} onClick={()=>void commit()}>{busy?'Working…':proposal.mode==='existing'?'Recheck existing stories':'Save additions and reassess'}</button></>}
    </section>}
    <fieldset disabled={busy||blocked}>
      <legend>Continue the review</legend>
      <label>What would you like to do?<select value={mode} onChange={e=>setMode(e.target.value as 'new'|'existing')}>
        <option value="new">Add experience that is not documented</option><option value="existing">Recheck evidence already in my stories</option>
      </select></label>
      <details><summary>Focus on requirements ({selected.length||'all'})</summary><div className="review-requirements">{requirements.map(r=><label key={r.id}><input type="checkbox" checked={selected.includes(r.id)} onChange={e=>setSelected(e.target.checked?[...selected,r.id]:selected.filter(id=>id!==r.id))}/>{r.interpretation}</label>)}</div></details>
      <label>Your context<textarea ref={composer} rows={5} maxLength={12000} value={text} onChange={e=>setText(e.target.value)} placeholder="Tell me which employer/project this belongs to, what you did, and what the current analysis missed. You can describe more than one experience."/></label>
      <label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/> Allow Jev to classify this account and propose its employer/project. Your words are saved only after your approval.</label>
      <button disabled={!chat||!consent||text.trim().length<3} onClick={()=>void send()}>{busy?'Reviewing…':'Send for review'}</button>
      <p className="muted">Messages alone do not edit stories or change scores. You choose whether to save the proposed additions. Leaving a gap unchanged needs no action.</p>
    </fieldset>
    {(error||loadError)&&<p className="error" role="alert">{error||loadError}</p>}
  </section></dialog>,document.body)}</>;
}
