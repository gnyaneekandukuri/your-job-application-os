import { useQuery, useQueryClient } from '@tanstack/react-query';
import { MarkApplied } from './MarkApplied';
import { TailorResume } from './TailorResume';

type Ready={job_id:string;company:string;title:string;url:string;score:number|null;recommendation:string|null;to_answer:number;keywords_added:number;downloaded:boolean;updated_at:string};

// The daily apply list: every prepared resume not yet applied to, best fit first.
export function ReadyToApply(){
 const query=useQueryClient();
 const {data,error,isLoading}=useQuery({queryKey:['ready-to-apply'],refetchInterval:15000,queryFn:async()=>{
  const response=await fetch('/api/ready-to-apply');if(!response.ok)throw Error('Could not load prepared resumes.');
  return (await response.json()).items as Ready[];
 }});
 if(isLoading)return <p>Loading prepared resumes…</p>;
 if(error)return <p role="alert">{String(error)}</p>;
 const rows=data||[];
 return <section className="ready-to-apply" aria-label="Ready to apply">
  <p>{rows.length?`${rows.length} prepared ${rows.length===1?'resume':'resumes'} waiting, best fit first. Review the green keyword words, answer any questions, download, then open the job and apply. Click I applied to take it off this list.`:'No prepared resumes waiting. Tailor a resume from Analyzed jobs and it appears here until you mark it applied.'}</p>
  {rows.length>0&&<table className="ready-table"><thead><tr><th>Job</th><th>Fit</th><th>Status</th><th>Keywords added</th><th></th></tr></thead><tbody>
   {rows.map(r=><tr key={r.job_id}>
    <td><strong>{r.company}</strong><br/>{r.title}</td>
    <td>{r.score!=null?`${Math.round(r.score)}%`:'–'}{r.recommendation?<><br/><span className="ready-note">{r.recommendation}</span></>:null}</td>
    <td>{r.to_answer?`${r.to_answer} to answer`:'Ready'}{r.downloaded?<><br/><span className="ready-note">PDF downloaded</span></>:null}</td>
    <td>{r.keywords_added||'–'}</td>
    <td className="ready-actions"><TailorResume jobId={r.job_id} label={`${r.company} · ${r.title}`} buttonLabel="Review & download"/>{/^https?:\/\//.test(r.url)&&<a href={r.url} target="_blank" rel="noreferrer">Open job ↗</a>}<MarkApplied jobId={r.job_id} company={r.company} onRecorded={()=>{query.setQueryData<Ready[]>(['ready-to-apply'],rows=>(rows||[]).filter(x=>x.job_id!==r.job_id));void query.invalidateQueries({queryKey:['ready-to-apply']});}}/></td>
   </tr>)}
  </tbody></table>}
 </section>;
}
