import type { Explanation } from './assessmentQueue';
export function RecommendationWhy({value}:{value:Explanation}) {
 return <><p>{value.summary}</p><details><summary>Evidence and gaps</summary>
 {value.strengths.length>0&&<><h4>Experience to emphasize</h4>{value.strengths.map(r=><article key={r.requirement_id}><strong>{r.requirement}</strong><p>{r.alignment==='partial'?'Partial support':'Strong support'}{r.tentative?' · tentative classification':''}</p>{r.citations.map((c,i)=><div key={i}><small>{c.source_title}{c.start_line?` · line ${c.start_line}`:''}</small><blockquote>{c.quote}</blockquote></div>)}</article>)}</>}
 {value.gaps.length>0&&<><h4>Gaps to address</h4>{value.gaps.map(r=><p key={r.requirement_id}><strong>{r.requirement}</strong> — {r.detail}{r.tentative?' Classification is tentative.':''}</p>)}</>}
 <h4>Decision criteria</h4><ul>{value.decision_basis.map((c,i)=><li key={i}>{c.label}: {c.detail} ({c.passed?'met':'not met'})</li>)}</ul>
 </details></>;
}
