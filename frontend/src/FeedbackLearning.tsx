import { useEffect, useState } from 'react';
type Feedback={id:string;state:string;error?:string;impact?:string;
  choice:{choice:string;reason:string};
  proposal:null|{scope:string;diagnosis:string;explanation:string;question:string;expected_impact:string};
  previews:{target:string;path:string;file:string;before:unknown;after:unknown;reason:string;supporting_quote:string}[];
  impact_preview?:{before_recommendation:string;before_score:number;after_recommendation:string|null;after_score:number|null;note:string};};
export function FeedbackLearning({jobId,version,choice,onChanged,onReviewEvidence}:{jobId:string;version:string|null;choice:{choice:string;reason:string;decided_at:string}|null;onChanged:()=>void;onReviewEvidence:()=>void}) {
  const [record,setRecord]=useState<Feedback|null>(null);
  const [busy,setBusy]=useState(false);
  const [consent,setConsent]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{let cancelled=false;void fetch(`/api/jobs/${jobId}/feedback`).then(async r=>{if(!r.ok)throw new Error('Could not load feedback history.');const data=await r.json();if(!cancelled)setRecord(data);}).catch(e=>{if(!cancelled)setError(String(e));});return()=>{cancelled=true;};},[jobId,choice?.decided_at]);
  async function submit(action?:'apply'|'dismiss'){
    if(busy)return;setBusy(true);setError('');
    try{
      const response=await fetch(`/api/jobs/${jobId}/feedback${action?'/resolve':''}`,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},
        body:JSON.stringify(action?{request_id:record!.id,action}:{request_id:crypto.randomUUID(),version,consent_to_send:true})});
      const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Could not review feedback.');setRecord(data);
      if(action==='apply')onChanged();
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  if(!choice&&!record&&!error)return null;
  return <section aria-label="Learn from your decision" className="feedback-learning">
    <h4>Learn from your decision</h4>
    {choice&&<>
      <p>{choice.reason.trim()?`Your explanation: ${choice.reason}`:'Why did you choose this pursuit level? Add and save your reason above so we can evaluate what should change.'}</p>
      <p>We can keep this as a one-role exception or propose a reusable correction. Your choice alone does not increase your score.</p>
      <label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/> Allow Jev to classify your explanation and propose explicit preference changes for approval.</label>
      <button disabled={busy||!version||!consent||choice.reason.trim().length<3||record?.state==='APPLYING'} onClick={()=>void submit()}>{busy?'Reviewing…':'Review my feedback'}</button>
    </>}
    {record&&<>
      {record.proposal&&<>
        <strong>{({role_only:'One-role exception',reusable:'Reusable correction',needs_evidence:'Evidence review needed',needs_clarification:'Clarification needed'} as Record<string,string>)[record.proposal.scope]}</strong>
        <p>{record.proposal.diagnosis}</p><p>{record.proposal.explanation}</p>
        {record.proposal.question&&<p><strong>Question:</strong> {record.proposal.question} Update and save your reason above, then review it again.</p>}
        <p>{record.proposal.expected_impact}</p>
        {record.proposal.scope==='role_only'&&<p>Your saved choice remains specific to this role. No global rule change is proposed.</p>}
        {record.proposal.scope==='needs_evidence'&&<button onClick={onReviewEvidence}>Review missing or disputed evidence</button>}
        {record.previews.map((p,i)=><article key={i}><strong>{p.target} · {p.path}</strong><small>{p.file}</small>
          <p>Before: {JSON.stringify(p.before)}</p><p>Proposed: {JSON.stringify(p.after)}</p><p>{p.reason}</p><blockquote>{p.supporting_quote}</blockquote></article>)}
        {record.impact_preview&&<p>{record.impact_preview.note}{record.impact_preview.after_recommendation&&` Recommendation: ${record.impact_preview.before_recommendation} → ${record.impact_preview.after_recommendation}. Evidence score: ${record.impact_preview.before_score} → ${record.impact_preview.after_score}.`}</p>}
      </>}
      {['PROPOSED','APPLYING'].includes(record.state)&&record.previews.length>0&&<>
        <p>Approving changes updates the living configuration for future roles. Earlier versions and this explanation are retained. Affected assessments will need reassessment.</p>
        <button disabled={busy} onClick={()=>void submit('apply')}>{record.state==='APPLYING'?'Retry approved update':'Approve these changes'}</button>
        {record.state!=='APPLYING'&&<button disabled={busy} onClick={()=>void submit('dismiss')}>Keep criteria unchanged</button>}
      </>}
      {record.state==='APPLIED'&&<p role="status">Changes saved. {record.impact}</p>}
      {record.state==='DISMISSED'&&<p role="status">Criteria kept unchanged. Your feedback is retained.</p>}
      {record.state==='FAILED'&&<p role="alert">{record.error}</p>}
    </>}
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
