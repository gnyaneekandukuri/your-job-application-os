import {useState} from 'react';
import type {Gap} from './gapReviewLogic';

export function GapReview({gaps,onReview,onPreferenceReview}:{gaps:Gap[];onReview:(id:string,question:string)=>void;onPreferenceReview:()=>void}){
  const [skipped,setSkipped]=useState(false);
  if(!gaps.length)return null;
  if(skipped)return <p className="gap-review-kept">Review questions left unanswered; score unchanged. <button onClick={()=>setSkipped(false)}>Show questions</button></p>;
  function card(gap:Gap){return <article key={gap.id} className={`gap-review-card gap-${gap.kind}`}>
    <span className="requirement-kind">{gap.label}</span><h4>{gap.title}</h4>
    <p>{gap.reason}</p><p className="muted">{gap.impact}</p>
    <p><strong>Question:</strong> {gap.question}</p>
    {gap.requirementId?<button onClick={()=>onReview(gap.requirementId,gap.question)}>Review this point</button>:<button onClick={onPreferenceReview}>Review preference or decision</button>}
    <details><summary>Source evidence</summary>{gap.jdQuote?<><strong>Job description</strong><blockquote>{gap.jdQuote}</blockquote></>:<p>No explicit JD wording establishes this condition.</p>}
      {gap.evidence.map((e,i)=><div key={i}><strong>{e.source_title}</strong><blockquote>{e.quote}</blockquote><small>{e.source_path} · line {e.start_line}</small></div>)}
      {!gap.evidence.length&&gap.requirementId&&<p>No cited experience establishes this requirement yet.</p>}
    </details>
  </article>;}
  return <section className="gap-review" aria-label="Gaps and questions to review">
    <h3>What could change this assessment?</h3>
    <details><summary>Review {gaps.length} open points</summary>
    <p>Core work and explicit requirements come first. Answer any that matter; leaving them unanswered keeps the score.</p>
    {gaps.slice(0,3).map(card)}
    {gaps.length>3&&<details><summary>Show {gaps.length-3} more review points</summary>{gaps.slice(3).map(card)}</details>}
    <button onClick={()=>setSkipped(true)}>Leave unanswered for now</button>
    </details>
  </section>;
}
