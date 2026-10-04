import { useQuery } from '@tanstack/react-query';

type Source = {id:string;name:string;kind:string;url:string;state:string;checked_at?:string;last_success?:string;error?:string;items:{title:string;url:string;published_at?:string}[]};
export function SourceLeads() {
 const query=useQuery({queryKey:['source-leads'],queryFn:async({signal})=>{
  const response=await fetch('/api/discovery/leads',{signal});
  if(!response.ok)throw new Error('Could not load company and newsletter sources.');
  return response.json() as Promise<Source[]>;
 },refetchInterval:15000});
 return <section className="discovery-notes" aria-labelledby="source-leads-heading">
  <h2 id="source-leads-heading">Company and newsletter leads</h2>
  <p>Find companies and openings to explore. These are separate from Your jobs and do not trigger paid analysis.</p>
  {query.error&&<p role="alert">{query.error.message}</p>}
  {query.data?.map(source=><details key={source.id}>
   <summary>{source.name} · {source.items.length} {source.kind.toLowerCase()} · {source.state==='checked'?'Checked':source.state==='error'?'Needs attention':'Not checked yet'}</summary>
   <p>{source.checked_at?`Last checked ${new Date(source.checked_at).toLocaleString()}. `:'Checked during the next full discovery run. '}
    <a href={source.url} target="_blank" rel="noreferrer">Open source</a></p>
   {source.error&&<p role="alert">{source.error} {source.last_success&&`Showing results saved ${new Date(source.last_success).toLocaleString()}.`}</p>}
   <p>{source.id==='nextplay'?'Public newsletter issues; some contain previews only. Issue dates are not job-posting dates.':source.id==='ramp'?'Vendor adoption reports identify companies to research; they do not confirm hiring or funding.':'Curated companies; inclusion does not confirm a current opening.'}</p>
   <ul>{source.items.map(item=><li key={item.url}><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>{item.published_at&&` · ${new Date(item.published_at).toLocaleDateString()}`}</li>)}</ul>
  </details>)}
 </section>;
}
