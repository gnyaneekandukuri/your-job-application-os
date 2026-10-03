import { useEffect, useRef, useState } from "react";
type KeywordSource = {id:string;title:string;employer:string;project:string;path:string;line:number;excerpt:string};
type KeywordProfile = {experience:{term:string;sources:KeywordSource[]}[];target:{roles:string[];keywords:string[];version:string}};
type KeywordMatch = {reason?:string;priority:"high"|"worth_checking"|"low";label:string;experience:{term:string;sources:KeywordSource[];listing:{field:string;excerpt:string}}[];target:{term:string;field:string;excerpt:string}[];different_focus:string[]};
type Assessment = {state:string;stage?:string;error?:string;score:number|null;provisional?:boolean;recommendation:string|null;choice?:string|null;recommendation_state?:string;recommendation_error?:string};
type JDDraft = {url:string;id:string;expected_hash:string;title:string;company:string;description:string;extraction_id?:string;warnings?:string[]};
type CompanyFit={group:string;label:string;sort_order:number;backing_boost:number;backing_reported:boolean;reason:string;evidence:{quote:string;url?:string;checked_at?:string}[]};
type Item = {company_fit?:CompanyFit;selected_assessment?:{state:string;error:string}|null;role_classification?:{labels:Record<string,string>;model:string;checked_at:string}|null;queued_for_assessment?:boolean;description_fetch?:{state:string;error:string}|null;automatic_assessment?:{state:string;error:string}|null;assessment?:Assessment;content_hash:string;keyword_match?:KeywordMatch;location_match?:{status:"match"|"outside"|"unclear";reason:string};id:string;title:string;company:string;location:string;url:string;description:string;posted_at:string|null;date_label:string;date_basis:string;funding:string;freshness:string;first_seen:string;last_seen:string;job_id:string|null;description_changed:boolean;assessment_state:string|null;sources:{board:string}[]};
type Diagnostic = {reason:string;kind:string;http_status?:number;host:string;path:string;attempts:number;causes:string[]};
type Source = {checked_at?:string;state:string;count?:number;error?:string;coverage?:string;warnings?:(string | (Diagnostic & {listing:string}))[];diagnostic?:Diagnostic};
type YCConnection = {state:string;message:string;busy:boolean;checked_at?:string};
type State = {recent_scans?:{started:string;finished:string|null;state:string;new_count?:number}[];pipeline?:{complete:number;running:number;attention:number;previous:number;queued:number};company_preference?:{focus:string;preferred_stages:string[]};jev?:{configured:boolean;message:string};yc_connection?:YCConnection;keyword_profile?:KeywordProfile;location_preference?:{label:string;countries:string[]};items:Item[];running:boolean;settings:{enabled:boolean;auto_assess:boolean};boards:Record<string,string>;unavailable:Record<string,string>;timezone:string;schedule:string[];scan:null|{started:string;finished:string|null;payload:{phase?:string;classification?:{state:string;classified:number;cached:number;skipped:number;error?:string};descriptions?:{fetched:number;needs_paste:number};state:string;new_count?:number;sources:Record<string,Source>;error?:string}}};
async function request<T>(path="",body?:unknown,signal?:AbortSignal):Promise<T> {
 const response=await fetch(`/api/discovery${path}`,body===undefined?{signal}:{method:"POST",headers:{"Content-Type":"application/json","X-Local-Review":"1"},body:JSON.stringify(body)});
 const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==="string"?value.detail:"Action failed. Try again.");return value;
}
const statusLabel=(state:string):string=>({complete:"Completed",incomplete:"Incomplete coverage",failed:"Failed",blocked:"Access denied / limited",partial:"Partial coverage",checked:"Checked",login_required:"Sign in to YC",busy:"YC browser busy"} as Record<string,string>)[state]??state;
const when=(date:string)=>new Date(date).toLocaleString();
const freshnessLabel=(item:Item)=>item.freshness==="within_24h"?"Source reports <24h":item.freshness==="older"?"Older than 24h":item.freshness==="date_only"?"Date only - age uncertain":"Date unknown";
export function Discovery({onOpen,onResults,initialReviewId,view="opportunities"}:{initialReviewId?:string|null;onResults:(tab?:string)=>void;view?:"runs"|"opportunities";onOpen:(jobId:string)=>Promise<void>}) {
 const [state,setState]=useState<State|null>(null);
 const [selected,setSelected]=useState<Record<string,string>>({});
 const [selectionNotice,setSelectionNotice]=useState("");
 const [companyFilter,setCompanyFilter]=useState("focus");
 const [priorityFilter,setPriorityFilter]=useState("all");
 const [page,setPage]=useState(1);
 const [error,setError]=useState("");
 const [busy,setBusy]=useState(false);
 const [filter,setFilter]=useState("all");
 const [query,setQuery]=useState("");
 const [connectionError,setConnectionError]=useState("");
 const [boardFilter,setBoardFilter]=useState("all");
 const [locationFilter,setLocationFilter]=useState("match");
 const [editingTarget,setEditingTarget]=useState(false);
 const [targetRoles,setTargetRoles]=useState("");
 const [targetKeywords,setTargetKeywords]=useState("");
 const [targetVersion,setTargetVersion]=useState("");
 const [jd,setJD]=useState<JDDraft|null>(null);
 const jdDialog=useRef<HTMLDialogElement>(null);
 const openedReview=useRef<string|null>(null);
 useEffect(()=>{if(initialReviewId&&state&&openedReview.current!==initialReviewId){const item=state.items.find(i=>i.id===initialReviewId);if(item){openedReview.current=initialReviewId;openJD(item);}}},[initialReviewId,state]);
 useEffect(()=>{if(jd)jdDialog.current?.showModal();else jdDialog.current?.close();},[jd?.id]);
 useEffect(()=>{let live=true;let timer:ReturnType<typeof setTimeout>;const controller=new AbortController();const load=async()=>{let interval=30000;try{const value=await request<State>("",undefined,controller.signal);if(live){setState(value);setConnectionError("");if(value.running||value.items.some(i=>i.assessment?.state==="RUNNING"||["QUEUED","RUNNING"].includes(i.selected_assessment?.state??"")))interval=4000;}}catch(e){if(live){setConnectionError(String(e));interval=5000;}}finally{if(live)timer=setTimeout(()=>void load(),document.hidden?60000:interval);}};void load();return()=>{live=false;controller.abort();clearTimeout(timer);};},[]);
 useEffect(()=>setPage(1),[query,priorityFilter,locationFilter,boardFilter,filter,companyFilter]);
 async function act(work:()=>Promise<void>){setBusy(true);setError("");try{await work();}catch(e){setError(e instanceof Error?e.message:"Action failed");}finally{setBusy(false);}}
 function openJD(item:Item){setError("");setJD({url:item.url,id:item.id,expected_hash:item.content_hash,title:item.title,company:item.company,description:item.description});}
 async function assess(item:Item){await request<{job_id:string}>(`/${item.id}/assess`,{});setState(await request<State>());}
 async function analyzeSelected(){
  const result=await request<{accepted:string[];rejected:{id:string;reason:string}[]}>("/assess-selected",{roles:Object.entries(selected).map(([id,content_hash])=>({id,content_hash}))});
  setSelected(current=>Object.fromEntries(Object.entries(current).filter(([id])=>!result.accepted.includes(id))));
  setSelectionNotice(`${result.accepted.length} roles queued. ${result.rejected.map(r=>`${state?.items.find(i=>i.id===r.id)?.company??'Role'}: ${r.reason}`).join(' ')}`);
  setState(await request<State>());
 }
 const queueActive=(item:Item)=>['QUEUED','RUNNING'].includes(item.selected_assessment?.state??'');
 const selectable=(item:Item)=>item.keyword_match?.priority==='worth_checking'&&item.description.length>=200&&!item.description_changed&&!item.queued_for_assessment&&!queueActive(item)&&!['RUNNING','COMPLETE'].includes(item.assessment?.state??'');
 const companyFocus=state?.company_preference?.focus??'any';
 const companyMatches=(item:Item)=>companyFilter==='all'||(companyFilter==='focus'?(companyFocus==='any'||(companyFocus==='startups'?['preferred','other_startup'].includes(item.company_fit?.group??'unknown'):item.company_fit?.group==='established')):companyFilter==='startups'?['preferred','other_startup'].includes(item.company_fit?.group??'unknown'):item.company_fit?.group===companyFilter);
 const rows=(state?.items??[]).filter(companyMatches).filter(item=>item.assessment?.state!=="COMPLETE").filter(item=>priorityFilter==="all"||(priorityFilter==="not_screened"?item.keyword_match?.label==="Not screened":item.keyword_match?.priority===priorityFilter&&item.keyword_match?.label!=="Not screened")).filter(item=>(locationFilter==="all"||(item.location_match?.status??"unclear")===locationFilter)&&(boardFilter==="all"||item.sources.some(s=>s.board===boardFilter))&&(filter==="all"||(filter==="recent"?item.freshness==="within_24h":filter==="discovered"?Date.now()-Date.parse(item.first_seen)<86400000:filter==="uncertain"?["date_only","unknown"].includes(item.freshness):item.description.length<200))&&`${item.title} ${item.company} ${item.location}`.toLowerCase().includes(query.toLowerCase()));
 if(companyFocus!=='any')rows.sort((a,b)=>(companyFocus==='startups'?(a.company_fit?.sort_order??100)-(b.company_fit?.sort_order??100):0)||((b.company_fit?.backing_boost??0)-(a.company_fit?.backing_boost??0)));
 const pages=Math.max(1,Math.ceil(rows.length/25));
 const currentPage=Math.min(page,pages);
 const pageRows=rows.slice((currentPage-1)*25,currentPage*25);
 return <section className="panel discovery">
  <div className="section-heading"><div><div className="eyebrow">LOCAL JOB DISCOVERY</div><h1>{view==="runs"?"Discovery runs":"New opportunities"}</h1><p>{view==="runs"?"Check job boards and review the latest run.":"Find roles that overlap with your experience and what you want to do next."}</p></div>
  {view==="runs"&&<button className="primary" disabled={busy||state?.running} onClick={()=>void act(async()=>setState(await request<State>("/check",{})))}>{state?.running?(state.scan?.payload.phase==="assessing"?"Assessing High priority jobs...":"Checking boards..."):"Check now"}</button>}</div>
  {error&&<p className="error" role="alert">{error}</p>}{connectionError&&<p className="error" role="alert">Connection interrupted. Retrying... {connectionError}</p>}
  {state && !state.running && ["failed","incomplete"].includes(state.scan?.payload.state??"") && <p className="discovery-coverage" role="status">{view==="opportunities"?"Some boards were unavailable. See Discovery runs for coverage.":"The last scan has incomplete coverage. Saved listings are retained."}</p>}
  {!state?<p>Loading discovery...</p>:view==="runs"?<>
   <p><a href="/#usage">View scan costs</a></p>
   <div className="discovery-run" aria-label="Last discovery run">
    <div><span>Last run started</span><strong>{state.scan?when(state.scan.started):"Not run yet"}</strong></div>
    <div><span>Finished</span><strong>{state.scan?.finished?when(state.scan.finished):state.running?"In progress":"—"}</strong></div>
    <div><span>Run status</span><strong>{state.running?(state.scan?.payload.phase==="assessing"?"Assessing jobs":"Running"):statusLabel(state.scan?.payload.state??"Not started")}</strong></div>
    <div><span>New this run</span><strong>{state.scan?.payload.new_count??"—"}</strong></div>
   </div>
   <p>{state.jev?.message}</p>{state.scan?.payload.classification && <p>Jev: {state.scan.payload.classification.state.replaceAll("_"," ")} · {state.scan.payload.classification.classified} classified · {state.scan.payload.classification.cached} reused{state.scan.payload.classification.error && ` — ${state.scan.payload.classification.error}`}</p>}
   <p className="discovery-clock">Times shown in your browser's local timezone. Scheduled checks: {state.settings.enabled?state.schedule.join(" / "):"Paused"} ({state.timezone}, server clock).</p>
   {state.yc_connection && <div className="discovery-preferences">
    <strong>YC · Work at a Startup — {({CONNECTED:"Connected",NOT_CONNECTED:"Not connected",LOGIN_REQUIRED:"Sign in again",WAITING_FOR_LOGIN:"Waiting for sign-in",CONNECTING:"Connecting",SCANNING:"Collecting",NEEDS_CHECK:"Check connection",BLOCKED:"Access denied",ERROR:"Connection error",SETUP_REQUIRED:"Setup required"} as Record<string,string>)[state.yc_connection.state]??state.yc_connection.state}</strong>
    <p>{state.yc_connection.message}</p>
    <button disabled={busy||state.yc_connection.busy||state.running} onClick={()=>void act(async()=>{const connection=await request<YCConnection>("/yc/connect",{});setState({...state,yc_connection:connection});})}>{state.yc_connection.state==="CONNECTED"?"Reconnect YC":"Connect YC"}</button>
    <button disabled={busy||state.yc_connection.busy||state.running} onClick={()=>void act(async()=>{const connection=await request<YCConnection>("/yc/check",{});setState({...state,yc_connection:connection});})}>Check connection</button>
    <button disabled={busy||state.yc_connection.busy||state.running||state.yc_connection.state!=="CONNECTED"} onClick={()=>void act(async()=>setState(await request<State>("/yc/scan",{})))}>Check YC now</button>
    <details><summary>How sign-in works</summary><p>Connect YC opens a separate Microsoft Edge window. Sign in there; it closes once the directory is verified. The app keeps that browser profile in your private workspace and uses it for scheduled checks. If YC expires the session, reconnect here. No password is requested by this app.</p></details>
   </div>}
   <h2>Job boards</h2><button disabled={busy||state.running} onClick={()=>void act(async()=>setState(await request<State>("/linkedin/scan",{})))}>Check LinkedIn now</button><div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Job board run summary"><table className="discovery-table board-overview"><thead><tr><th>Board</th><th>Last check</th><th>Status</th><th>Found this run</th><th>Saved</th></tr></thead><tbody>
   {Object.entries(state.boards).map(([id,name])=>{const source=state.scan?.payload.sources[id];return <tr key={id}><th scope="row">{name}</th><td>{source?.checked_at?when(source.checked_at):"Not checked this run"}</td><td>{statusLabel(source?.state??(state.running?"Waiting":"Not checked"))}</td><td>{source?.count??"—"}</td><td>{state.items.filter(item=>item.sources.some(s=>s.board===id)).length}</td></tr>;})}
   </tbody></table></div>
   <details className="retrieval-advanced"><summary>Schedule and assessment settings</summary>
    <p>Checks at {state.schedule.join(", ")} using your computer's local clock ({state.timezone}). Keep the local server running and laptop awake. One catch-up check runs after a missed slot when the server is available. Temporary connection failures retry after 10, 30 and 60 seconds. Access denials are not retried during the scan.</p>
    <label className="retrieval-simple-consent"><input type="checkbox" disabled={busy} checked={state.settings.enabled} onChange={e=>{const enabled=e.target.checked;void act(async()=>setState(await request<State>("/settings",{auto_assess:state.settings.auto_assess,enabled})));}}/>Enable local scheduled checks</label>
    <label className="retrieval-simple-consent"><input type="checkbox" disabled={busy} checked={state.settings.auto_assess} onChange={e=>{const auto_assess=e.target.checked;void act(async()=>setState(await request<State>("/settings",{enabled:state.settings.enabled,auto_assess})));}}/>Automatically assess High priority country-matched jobs with Jev after each scan (full JD required; TypeSafe usage applies).</label>
    <p><small>Assessment sends job text and retrieved career evidence to TypeSafe. OpenAI creates reusable search embeddings; Jev assesses selected evidence. Posting age does not exclude a High priority role seen in the scan. Failed or interrupted assessments require manual attention.</small></p>
   </details>
   <details className="discovery-notes"><summary>Coverage limits and unavailable boards</summary><p>Counts are PM candidates, not verified matches. Shared jobs can count toward more than one board.</p>
    {Object.entries(state.boards).map(([id,name])=>{const source=state.scan?.payload.sources[id];return source&&<p key={id}><strong>{name}:</strong> {source.diagnostic?.reason||source.error||source.coverage}{source.diagnostic && <small> {source.diagnostic.host}{source.diagnostic.path} · {source.diagnostic.attempts} attempts · {source.diagnostic.causes.join(" / ")}</small>}{source.warnings?.length?` ${source.warnings.map(w=>typeof w==="string"?w:`${w.listing}: ${w.reason} (${w.attempts} attempts)`).join("; ")}`:""}</p>;})}
    {Object.entries(state.unavailable).map(([id,reason])=><p key={id}><strong>{id.replaceAll("_"," ")}</strong>: {reason}; not enabled.</p>)}<p>Funding, location eligibility and role suitability still need verification.</p>
   </details>
   {state.scan?.payload.descriptions && <p>{state.scan.payload.descriptions.fetched} descriptions retrieved · {state.scan.payload.descriptions.needs_paste} need a pasted JD</p>}
   {state.scan?.payload.error&&<p role="alert">{state.scan.payload.error}</p>}{state.running&&<p role="status">{state.scan?.payload.phase==="assessing" ? "Collection finished. High priority assessments are being started one at a time." : state.scan?.payload.phase==="classifying" ? "Classifying role responsibilities with Jev." : state.scan?.payload.phase==="descriptions" ? "Retrieving missing job descriptions before prioritizing." : "Checking sources. Results appear in New opportunities as each board finishes."}</p>}
  </>:<>
   {state.keyword_profile && <details className="discovery-preferences"><summary>Your preferences</summary>
    <p><strong>Location:</strong> {state.location_preference?.label || "Preferred country"}</p>
    <p><strong>Company focus:</strong> {companyFocus}{companyFocus==='startups'&&` · ${state.company_preference?.preferred_stages.join(' → ')}`}. Edit in Your preferences.</p>
    <p><strong>Target roles:</strong> {state.keyword_profile.target.roles.join(", ")}</p>
    <p><strong>Target keywords:</strong> {state.keyword_profile.target.keywords.join(", ") || "None added"}</p>
    {!editingTarget ? <button onClick={()=>{setTargetRoles(state.keyword_profile!.target.roles.join("\n"));setTargetKeywords(state.keyword_profile!.target.keywords.join(", "));setTargetVersion(state.keyword_profile!.target.version);setEditingTarget(true);}}>Edit targets</button> : <form onSubmit={e=>{e.preventDefault();void act(async()=>{setState(await request<State>("/target",{roles:targetRoles.split("\n").map(s=>s.trim()).filter(Boolean),keywords:targetKeywords.split(",").map(s=>s.trim()).filter(Boolean),expected_version:targetVersion}));setEditingTarget(false);});}}>
     <label>Target roles (one per line)<textarea value={targetRoles} onChange={e=>setTargetRoles(e.target.value)} required/></label>
     <label>Target keywords (comma separated)<textarea value={targetKeywords} onChange={e=>setTargetKeywords(e.target.value)}/></label>
     <button disabled={busy} type="submit">Save targets</button> <button disabled={busy} type="button" onClick={()=>setEditingTarget(false)}>Cancel</button>
    </form>}

   </details>}
   <div className="pipeline-summary" aria-label="Discovery and assessment overview">
    <p>{state.scan?<>Latest scan: {when(state.scan.started)} · {state.scan.payload.new_count??'—'} newly discovered · {statusLabel(state.scan.payload.state)}</>:'No scan yet'}</p>
    <div className="pipeline-counts">
     <button onClick={()=>setPriorityFilter('high')}><strong>{rows.filter(i=>i.keyword_match?.priority==='high').length}</strong> High priority</button>
     <button onClick={()=>setPriorityFilter('worth_checking')}><strong>{rows.filter(i=>i.keyword_match?.priority==='worth_checking'&&i.keyword_match.label!=='Not screened').length}</strong> Worth checking</button>
     <button onClick={()=>setPriorityFilter("not_screened")}><strong>{rows.filter(i=>i.keyword_match?.label==='Not screened').length}</strong> Not screened</button>
     <button onClick={()=>onResults("running")}><strong>{state.pipeline?.queued??0} / {state.pipeline?.running??0}</strong> Queued / running</button>
     <button onClick={()=>onResults()}><strong>{state.pipeline?.complete??0}</strong> Completed →</button>
     <button onClick={()=>onResults("attention")}><strong>{(state.pipeline?.attention??0)+(state.pipeline?.previous??0)}</strong> Needs attention</button>
    </div>
    <small>Priority counts reflect the filtered list below. Assessment counts cover all saved roles; {state.pipeline?.previous??0} outdated results are under Your jobs → Other activity.</small>
    <details><summary>Recent scans</summary>{state.recent_scans?.map(scan=><p key={scan.started}>{when(scan.started)} · {statusLabel(scan.state)} · {scan.new_count===undefined?'New-listing count unavailable':`${scan.new_count} newly discovered`}</p>)}<small>Scan counts are newly saved listings, before preference filtering. Historical assessment movement was not recorded per scan; current totals are shown above.</small></details>
   </div>
   <div className="opportunity-filters discovery-filters">
    <label>Search<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Company, role or location"/></label>
    <label>Priority<select value={priorityFilter} onChange={e=>setPriorityFilter(e.target.value)}><option value="all">All priorities</option><option value="worth_checking">Worth checking</option><option value="high">High priority</option><option value="not_screened">Not screened</option><option value="low">Skip</option></select></label>
    <label>Companies<select value={companyFilter} onChange={e=>setCompanyFilter(e.target.value)}><option value="focus">{companyFocus==='startups'?'Your startup focus':companyFocus==='established'?'Established companies':'All companies'}</option><option value="all">All companies</option><option value="preferred">Preferred stages</option><option value="startups">All reported startups</option><option value="established">Established companies</option><option value="unknown">Company / stage unknown</option></select></label>
    <label>Location<select value={locationFilter} onChange={e=>setLocationFilter(e.target.value)}><option value="match">{state.location_preference?.label || "Preferred country"} matches</option><option value="unclear">Location unclear</option><option value="all">All locations</option></select></label>
    <label>Job board<select value={boardFilter} onChange={e=>setBoardFilter(e.target.value)}><option value="all">All boards</option>{Object.entries(state.boards).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
    <label>Show<select value={filter} onChange={e=>setFilter(e.target.value)}>{[["all","All saved"],["recent","Source reports <24h"],["discovered","Discovered in last 24h"],["uncertain","Date uncertain"],["missing","Description needed"]].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
   </div>
   {companyFilter==='focus'&&companyFocus!=='any'&&<p className="discovery-clock">Showing your company focus. <button className="text-action" onClick={()=>setCompanyFilter('unknown')}>Review unknown companies</button> · <button className="text-action" onClick={()=>setCompanyFilter('all')}>Show all companies</button></p>}
   <div className="opportunity-toolbar">
    <div className="opportunity-count"><strong>{rows.length} roles</strong><span>{Object.keys(selected).length?`${Object.keys(selected).length} selected across all pages`:'Select roles to assess'}</span></div>
    <div className="selection-actions">{Object.keys(selected).length>0&&<button className="text-action" disabled={busy} onClick={()=>setSelected({})}>Clear</button>}<button className="primary" disabled={busy||!Object.keys(selected).length} onClick={()=>void act(analyzeSelected)}>Analyze selected{Object.keys(selected).length?` (${Object.keys(selected).length})`:''}</button><button className="text-action" onClick={()=>onResults()}>Your jobs →</button></div>
   </div>
   {selectionNotice&&<p role="status">{selectionNotice}</p>}
   <div className="opportunity-pagination"><small>{rows.length?`${(currentPage-1)*25+1}–${Math.min(currentPage*25,rows.length)} of ${rows.length}`:'No matching roles'} · API usage applies when analyzing</small><div><button disabled={currentPage===1} onClick={()=>setPage(currentPage-1)}>Previous</button><span>Page {currentPage} of {pages}</span><button disabled={currentPage===pages} onClick={()=>setPage(currentPage+1)}>Next</button></div></div>
   {!rows.length?<p>{state.scan?"No saved opportunities match these filters. Try Company / stage unknown or All companies to review unconfirmed listings.":"Open Discovery runs and choose Check now to collect listings."}</p>:<div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="All opportunities"><table className="discovery-table opportunities-table"><thead><tr><th scope="col">Company / role</th><th scope="col">Priority / keywords</th><th scope="col">Link</th><th scope="col">Job board</th><th scope="col">Location</th><th scope="col">Posted / freshness</th><th scope="col">Analysis</th></tr></thead><tbody>
    {pageRows.map(item=><tr key={item.id}><td>{item.keyword_match?.priority==='worth_checking'&&<label className="select-role"><input type="checkbox" aria-label={`Select ${item.company} · ${item.title} for deep analysis`} disabled={busy||!selectable(item)||(!selected[item.id]&&Object.keys(selected).length>=100)} checked={!!selected[item.id]} onChange={e=>{const checked=e.target.checked;setSelectionNotice('');setSelected(current=>{const next={...current};if(checked)next[item.id]=item.content_hash;else delete next[item.id];return next;});}}/>Select</label>}<strong>{item.company}</strong>{item.company_fit&&<small title={item.company_fit.reason}>{item.company_fit.label}{item.company_fit.backing_reported?" · Backing reported":""}</small>}<div className="discovery-role">{item.title}</div>{item.role_classification && <small title={`Jev · ${item.role_classification.model} · advisory classification`}>{Object.values(item.role_classification.labels).join(" · ")}</small>}
     <details className="discovery-row-details"><summary>Job details</summary>{item.company_fit&&<><p>{item.company_fit.reason}</p>{item.company_fit.evidence.map((e,i)=><p key={i}><small>{e.quote} {e.url&&<a href={e.url} target="_blank" rel="noreferrer">Source</a>}{e.checked_at&&` · Checked ${e.checked_at}`}</small></p>)}</>}<p>Funding: {item.funding||"Unknown"}</p><p>First detected: {when(item.first_seen)}<br/>Last seen: {when(item.last_seen)}<br/>Sources: {item.sources.map(s=>state.boards[s.board]||s.board).join(", ")}</p>
     {item.date_basis==="board_timestamp"&&<p>Board timestamp; original publication time not verified.</p>}{item.description_changed&&<p className="notice">Description changed after import. Existing analysis has not been overwritten.</p>}{item.description.length>=20?<pre className="retrieval-source">{item.description}</pre>:<p>Full description needed. This board supplied metadata only.</p>}</details></td>
     <td>{item.keyword_match && <div className="opportunity-priority"><span className={`preference-badge preference-${item.keyword_match.priority}`} title={item.keyword_match.reason}>{item.keyword_match.label}</span>{item.keyword_match.reason&&<details><summary>Why this priority</summary><p>{item.keyword_match.reason}</p></details>}</div>}<div className="keyword-tags">{item.keyword_match?.experience.length ? item.keyword_match.experience.map(match=><span key={match.term}>{match.term}</span>) : <span className="muted">—</span>}</div></td>
     <td><a href={item.url} target="_blank" rel="noreferrer">View listing ↗</a></td>
     <td>{item.sources.map(s=>state.boards[s.board]||s.board).join(", ")}</td><td>{item.location||"Unknown"}</td>
     <td><span className="discovery-status">{freshnessLabel(item)}</span><small>{item.posted_at?(/^\d{4}-\d{2}-\d{2}$/.test(item.posted_at)?item.posted_at:when(item.posted_at)):"Not provided"}</small>{item.date_label&&<small>{item.date_label}</small>}<small>Discovered: {when(item.first_seen)}</small></td>
     <td>
      {item.description.length<200 && item.description_fetch?.state==="NEEDS_PASTE" && <small>Open the listing and paste its full JD.</small>}
      {item.selected_assessment?.state==="QUEUED"&&<strong role="status">Queued for deep analysis</strong>}
      {item.selected_assessment?.state==="RUNNING"&&item.assessment?.state!=="RUNNING"&&<strong role="status">Starting analysis</strong>}
      {item.selected_assessment?.state==="NEEDS_ATTENTION"&&item.selected_assessment.error&&<small role="status">{item.selected_assessment.error}</small>}
      {item.queued_for_assessment && <strong role="status">Queued for automatic analysis</strong>}
      {item.assessment?.state==="RUNNING" && <strong role="status">Analyzing</strong>}
      {["ERROR","INTERRUPTED","NEEDS_ATTENTION"].includes(item.assessment?.state??"") && <strong>Needs attention</strong>}
      {["ERROR","INTERRUPTED","STALE","NEEDS_ATTENTION"].includes(item.assessment?.state??"") && <button onClick={()=>onResults()}>View in Your jobs</button>}
      {item.description.length<200 || item.description_changed ? <button disabled={busy} onClick={()=>openJD(item)}>{item.description_changed ? "Review updated JD" : "Get full JD"}</button> : <>
       {!queueActive(item) && !item.queued_for_assessment && ["NOT_STARTED", ""].includes(item.assessment?.state ?? "") && <button disabled={busy} onClick={()=>void act(()=>assess(item))}>{item.job_id ? "Resume / reassess (paid)" : "Save & analyze (paid)"}</button>}
      </>}
      {item.job_id && item.assessment?.state==="NOT_STARTED" && <button disabled={busy} onClick={()=>void act(()=>onOpen(item.job_id!))}>Open role &amp; analysis</button>}
     </td>
    </tr>)}
   </tbody></table></div>}
  </>}
 <dialog ref={jdDialog} className="experience-dialog" aria-label="Review full job description" onCancel={e=>{if(busy)e.preventDefault();else setJD(null);}}>
  {jd && <form onSubmit={e=>{e.preventDefault();void act(async()=>{setState(await request<State>(`/${jd.id}/description`,{expected_hash:jd.expected_hash,title:jd.title,company:jd.company,description:jd.description,extraction_id:jd.extraction_id}));setJD(null);});}}>
   <h2>Review full job description</h2>
   {error && <p role="alert" className="error">{error}</p>}
   <fieldset disabled={busy}>
    <button type="button" onClick={()=>void act(async()=>{const fetched=await request<{expected_hash:string;title:string;company:string;description:string;extraction_id:string;warnings:string[]}>(`/${jd.id}/description/fetch`,{});setJD({...jd,...fetched,title:fetched.title||jd.title,company:fetched.company||jd.company});})}>Fetch from listing</button>
    <p><a href={jd.url} target="_blank" rel="noreferrer">Open this listing ↗</a>, then copy and paste the full job description below if fetching fails.</p>
    {jd.warnings?.map((warning,i)=><p key={i}>{warning}</p>)}
    <label>Company<input required maxLength={200} value={jd.company} onChange={e=>setJD({...jd,company:e.target.value})}/></label>
    <label>Role title<input required maxLength={200} value={jd.title} onChange={e=>setJD({...jd,title:e.target.value})}/></label>
    <label>Job description<textarea required minLength={20} maxLength={50000} rows={12} value={jd.description} onChange={e=>setJD({...jd,description:e.target.value})}/></label>
    <button type="submit">Save JD</button><button type="button" onClick={()=>setJD(null)}>Cancel</button>
   </fieldset>
  </form>}
 </dialog>
 </section>;
}
