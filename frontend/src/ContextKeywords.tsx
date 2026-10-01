import { useEffect, useState } from "react";
type Keyword = {term:string;sources:{id:string;title:string;employer:string;project:string;line:number;excerpt:string}[]};
export function ContextKeywords({revision}:{revision:string}) {
 const [keywords,setKeywords]=useState<Keyword[]>([]);
 const [selected,setSelected]=useState("");
 const [error,setError]=useState("");
 useEffect(()=>{let live=true;setKeywords([]);setError("");
  void fetch("/api/library/keywords").then(async response=>{if(!response.ok)throw new Error("Could not load your keywords.");return response.json();}).then(value=>{if(live)setKeywords(value.experience);}).catch(e=>{if(live)setError(e.message);});
  return()=>{live=false;};
 },[revision]);
 const active=keywords.find(keyword=>keyword.term===selected);
 return <section className="context-keywords" aria-label="Your skills and keywords">
  <h2>Your skills &amp; keywords</h2>
  {error ? <p role="alert">{error}</p> : <div className="keyword-tags">{keywords.map(keyword=><button key={keyword.term} aria-pressed={selected===keyword.term} onClick={()=>setSelected(selected===keyword.term?"":keyword.term)}>{keyword.term}</button>)}</div>}
  {active && <div className="keyword-evidence"><div className="section-heading"><h3>{active.term}</h3><button onClick={()=>setSelected("")}>Close</button></div>{active.sources.map(source=><div key={source.id}><strong>{source.employer} · {source.project}</strong><p>{source.excerpt}</p><small>{source.title} · line {source.line}</small></div>)}</div>}
 </section>;
}
