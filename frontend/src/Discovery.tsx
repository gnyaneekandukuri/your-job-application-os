import {MarkApplied} from './MarkApplied';
import {WritingHelp,isYCListing} from './TeamMessage';
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ScanReport, type ScanReportData } from "./ScanReport";
import { CompanyWatchlist } from "./CompanyWatchlist";
import { SourceLeads } from "./SourceLeads";
type KeywordSource = {id:string;title:string;employer:string;project:string;path:string;line:number;excerpt:string};
type KeywordProfile = {experience:{term:string;sources:KeywordSource[]}[];target:{roles:string[];keywords:string[];version:string}};
type KeywordMatch = {reason?:string;priority:"high"|"worth_checking"|"low";label:string;experience:{term:string;sources:KeywordSource[];listing:{field:string;excerpt:string}}[];target:{term:string;field:string;excerpt:string}[];different_focus:string[]};
type Assessment = {state:string;stage?:string;error?:string;score:number|null;provisional?:boolean;recommendation:string|null;choice?:string|null;recommendation_state?:string;recommendation_error?:string};
type JDDraft = {url:string;id:string;expected_hash:string;title:string;company:string;description:string;extraction_id?:string;warnings?:string[]};
type CompanyFit={group:string;label:string;sort_order:number;backing_boost:number;backing_reported:boolean;reason:string;evidence:{quote:string;url?:string;checked_at?:string}[]};
type Item = {passed?:boolean;duplicate_ids?:string[];needs_help?:{kind:string;message:string;issue?:string;todo?:string}[];listing_urls?:string[];kept?:boolean;company_watched?:boolean;retention_protected?:boolean;age_state?:string;company_fit?:CompanyFit;selected_assessment?:{state:string;error:string;position?:number|null}|null;role_classification?:{labels:Record<string,string>;model:string;checked_at:string}|null;queued_for_assessment?:boolean;description_fetch?:{state:string;error:string}|null;automatic_assessment?:{state:string;error:string}|null;assessment?:Assessment;content_hash:string;keyword_match?:KeywordMatch;location_match?:{status:"match"|"outside"|"unclear";reason:string};id:string;title:string;company:string;location:string;url:string;description_length:number;posted_at:string|null;date_label:string;date_basis:string;funding:string;freshness:string;linkedin_repost?:boolean;sponsorship?:{quote:string}|null;first_seen:string;last_seen:string;job_id:string|null;description_changed:boolean;assessment_state:string|null;sources:{board:string}[]};
type Diagnostic = {reason:string;kind:string;http_status?:number;host:string;path:string;attempts:number;causes:string[]};
type Source = {checked_at?:string;state:string;count?:number;error?:string;coverage?:string;warnings?:(string | (Diagnostic & {listing:string}))[];diagnostic?:Diagnostic};
type YCConnection = {state:string;message:string;busy:boolean;checked_at?:string};
type Overview={started:string;finished:string|null;state:string;tracked:boolean;seen:number;new:number|null;high:number;worth_checking:number;requested:number;completed:number;worth_applying:number;auto_paused:boolean;error?:string;failures:{board:string;state:string;reason:string}[]};
type State = {help_count?:number;automatic_running?:boolean;freshness_counts?:{recent:number;older:number;uncertain:number};scan_overview?:Overview;ready:boolean;revision:string;total:number;page:number;pages:number;board_counts:Record<string,number>;run_reports?:ScanReportData[];recent_scans?:{started:string;finished:string|null;state:string;new_count?:number}[];pipeline?:{complete:number;running:number;attention:number;previous:number;queued:number};company_preference?:{focus:string;preferred_stages:string[]};jev?:{configured:boolean;message:string};yc_connection?:YCConnection;lenny_connection?:YCConnection;keyword_profile?:KeywordProfile;location_preference?:{label:string;countries:string[]};items:Item[];running:boolean;settings:{enabled:boolean;auto_assess:boolean};boards:Record<string,string>;unavailable:Record<string,string>;timezone:string;schedule:string[];scan:null|{started:string;finished:string|null;payload:{description_progress?:{total:number;completed:number;current?:{company:string;title:string}};phase?:string;classification?:{state:string;classified:number;cached:number;skipped:number;error?:string};descriptions?:{fetched:number;needs_paste:number};state:string;new_count?:number;sources?:Record<string,Source>;error?:string}}};
async function request<T>(path="",body?:unknown,signal?:AbortSignal):Promise<T> {
 const response=await fetch(`/api/discovery${path}`,body===undefined?{signal}:{method:"POST",headers:{"Content-Type":"application/json","X-Local-Review":"1"},body:JSON.stringify(body)});
 const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==="string"?value.detail:"Action failed. Try again.");return value;
}
const statusLabel=(state:string):string=>({complete:"Completed",incomplete:"Incomplete coverage",failed:"Failed",blocked:"Access denied / limited",partial:"Partial coverage",checked:"Checked",login_required:"Sign in to YC",busy:"YC browser busy"} as Record<string,string>)[state]??state;
const when=(date:string)=>new Date(date).toLocaleString();
const freshnessLabel=(item:Item)=>item.date_basis==="hiring_comment_timestamp"?"Vacancy date unknown (HN comment dated below)":item.linkedin_repost?"LinkedIn repost/promoted - first posted ~30+ days ago":item.freshness==="within_24h"?"Source reports <24h":item.freshness==="older"?"Older than 24h":item.freshness==="date_only"?"Date only - age uncertain":"Date unknown";
/** Saves a listing as a role (free, no analysis) the first time writing help is opened. */
function RowWritingHelp({oid,jobId,company,url,yc,hasDescription}:{oid:string;jobId:string|null;company:string;url:string;yc:boolean;hasDescription:boolean}){
 const [id,setId]=useState(jobId||''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 if(id)return <WritingHelp jobId={id} company={company} url={url} yc={yc} defaultOpen={!jobId}/>;
 async function start(){setBusy(true);setError('');try{const r=await fetch(`/api/discovery/${oid}/save`,{method:'POST',headers:{'X-Local-Review':'1'}});const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Could not save this job.');setId(v.job_id);}catch(e){setError(e instanceof Error?e.message:'Could not save this job.');}finally{setBusy(false);}}
 return <><button disabled={busy||!hasDescription} title={hasDescription?'No deep analysis needed':'Add the full job description first'} onClick={()=>void start()}>{busy?'Opening…':'Writing help'}</button>{error&&<small role="alert">{error}</small>}</>;
}

export function Discovery({onOpen,onResults,onAdd,initialReviewId,view="opportunities"}:{onAdd:()=>void;initialReviewId?:string|null;onResults:(tab?:string)=>void;view?:"runs"|"opportunities";onOpen:(jobId:string)=>Promise<void>}) {
 const queryClient=useQueryClient();
 const [selected,setSelected]=useState<Record<string,string>>({});
 const [selectionNotice,setSelectionNotice]=useState("");
 const [companyFilter,setCompanyFilter]=useState("focus");
 const [priorityFilter,setPriorityFilter]=useState("all");
 const [page,setPage]=useState(1);
 const [tab,setTab]=useState<"jobs"|"help">("jobs");
 const [error,setError]=useState("");
 const [busy,setBusy]=useState(false);
 const [filter,setFilter]=useState("active");
 const [watchCompany,setWatchCompany]=useState<string|null>(null);
 const [query,setQuery]=useState("");

 const [boardFilter,setBoardFilter]=useState("all");
 const [locationFilter,setLocationFilter]=useState("match");
 const [editingTarget,setEditingTarget]=useState(false);
 const [targetRoles,setTargetRoles]=useState("");
 const [targetKeywords,setTargetKeywords]=useState("");
 const [targetVersion,setTargetVersion]=useState("");
 const [jd,setJD]=useState<JDDraft|null>(null);
 const jdDialog=useRef<HTMLDialogElement>(null);
 const openedReview=useRef<string|null>(null);
 const status=useQuery({queryKey:['job-list-status'],queryFn:({signal})=>request<{ready:boolean;revision:string;refreshing:boolean;error:string|null;running:boolean;automatic_running?:boolean}>('/status',undefined,signal),refetchInterval:4000,refetchIntervalInBackground:true});
 const [search,setSearch]=useState(query);
 useEffect(()=>{const timer=setTimeout(()=>setSearch(query),250);return()=>clearTimeout(timer);},[query]);
 const params=new URLSearchParams({page:String(page),query:search,priority:priorityFilter,location:locationFilter,company:companyFilter,board:boardFilter,show:tab==="help"?"help":filter,runs:String(view==='runs')});
 const listing=useQuery({queryKey:['job-list',view,params.toString(),status.data?.revision],queryFn:({signal})=>request<State>('/page?'+params.toString(),undefined,signal),enabled:!!status.data?.ready,placeholderData:(previous,previousQuery)=>previousQuery?.queryKey[1]===view?keepPreviousData(previous):undefined});
 const state=listing.data?.ready?{...listing.data,running:status.data?.running??listing.data.running,automatic_running:status.data?.automatic_running}:null;
 const connectionError=(status.error||listing.error)?.message;
 const updating=!!status.data?.refreshing||listing.isPlaceholderData;
 function setState(_ignored?:unknown){void queryClient.invalidateQueries({queryKey:['job-list-status']});void queryClient.invalidateQueries({queryKey:['job-list']});}
 useEffect(()=>{if(initialReviewId&&openedReview.current!==initialReviewId){openedReview.current=initialReviewId;void act(()=>openJD({id:initialReviewId}));}},[initialReviewId]);
 useEffect(()=>{if(jd)jdDialog.current?.showModal();else jdDialog.current?.close();},[jd?.id]);
 useEffect(()=>setPage(1),[query,priorityFilter,locationFilter,boardFilter,filter,companyFilter]);
 async function act(work:()=>Promise<void>){setBusy(true);setError("");try{await work();}catch(e){setError(e instanceof Error?e.message:"Action failed");}finally{setBusy(false);}}
 async function openJD(item:{id:string}){const detail=await request<JDDraft & {content_hash:string}>(`/${item.id}/detail`);setError("");setJD({...detail,expected_hash:detail.content_hash});}
 async function analyzeSelected(roles=selected){
  const result=await request<{accepted:string[];rejected:{id:string;reason:string}[]}>("/assess-selected",{roles:Object.entries(roles).map(([id,content_hash])=>({id,content_hash}))});
  setSelected(current=>Object.fromEntries(Object.entries(current).filter(([id])=>!result.accepted.includes(id))));
  setSelectionNotice(`${result.accepted.length} roles queued.${result.accepted.length&&state?.running ? " Analysis will start after the current scan finishes." : ""} ${result.rejected.map(r=>`${state?.items.find(i=>i.id===r.id)?.company??'Role'}: ${r.reason}`).join(' ')}`);
  setState();
 }
 const [rowNotice,setRowNotice]=useState<Record<string,string>>({});
 const [justQueued,setJustQueued]=useState<Record<string,boolean>>({});
 const [rowBusy,setRowBusy]=useState<Record<string,boolean>>({});
 // Listings whose description was just saved, keyed to the old content hash until the list refreshes.
 const [saved,setSaved]=useState<Record<string,string>>({});
 // Passed in this session; hidden at once instead of waiting for the list to refresh.
 const [hidden,setHidden]=useState<Record<string,boolean>>({});
 async function passRow(item:Item,passed:boolean){
  for(const id of item.duplicate_ids?.length?item.duplicate_ids:[item.id])await request(`/${id}/pass`,{passed});
  setHidden(h=>({...h,[item.id]:passed}));
  setRowNotice(n=>({...n,[item.id]:passed?'Passed. Find it under Show → Passed by me.':'Restored to New jobs.'}));
  setState();
 }
 // Queue in the background: only this row waits, the rest of the page stays usable.
 async function analyzeRow(item:Item){
  if(rowBusy[item.id])return;
  setRowBusy(b=>({...b,[item.id]:true}));setRowNotice(n=>({...n,[item.id]:'Queuing…'}));
  try{
   const result=await request<{accepted:string[];rejected:{id:string;reason:string}[];positions?:Record<string,number>}>("/assess-selected",{roles:[{id:item.id,content_hash:item.content_hash}]});
   const place=result.positions?.[item.id];
   if(result.accepted.length)setJustQueued(q=>({...q,[item.id]:true}));
   setRowNotice(n=>({...n,[item.id]:result.accepted.length?(state?.running?'Queued. Starts after the current scan.':place?`Queued for deep analysis · #${place} in line. It moves to Analyzed jobs when it finishes.`:'Queued for deep analysis.'):(result.rejected[0]?.reason||'Could not queue this job.')}));
   setState();
  }catch(e){setRowNotice(n=>({...n,[item.id]:e instanceof Error?e.message:'Could not queue this job.'}));}
  finally{setRowBusy(b=>({...b,[item.id]:false}));}
 }
 // A just-queued row hides its start buttons until the slower list refresh catches up.
 const queueActive=(item:Item)=>!!justQueued[item.id]||['QUEUED','RUNNING'].includes(item.selected_assessment?.state??'');
 const selectable=(item:Item)=>item.description_length>=200&&!item.description_changed&&!item.queued_for_assessment&&!queueActive(item)&&!['RUNNING','COMPLETE'].includes(item.assessment?.state??'');
 const companyFocus=state?.company_preference?.focus??'any';
 const total=state?.total??0;
 const pages=state?.pages??1;
 const currentPage=state?.page??page;
 const pageRows=(state?.items??[]).filter(item=>!(tab==="help"&&saved[item.id]&&saved[item.id]===item.content_hash)&&(filter==="passed"&&tab==="jobs"?hidden[item.id]!==false:!hidden[item.id]));
 return <section className="panel discovery">
  {view==='opportunities'&&<div className="section-heading"><div><h2>New jobs</h2><p>Found a role elsewhere? Add its link for deep analysis.</p></div><button className="primary" onClick={onAdd}>+ Add a job</button></div>}
  {view==="runs"&&<div className="section-heading"><div><div className="eyebrow">LOCAL JOB DISCOVERY</div><h1>{view==="runs"?"Discovery runs":"New opportunities"}</h1><p>{view==="runs"?"Check job boards and review the latest run.":"Find roles that overlap with your experience and what you want to do next."}</p></div>
  {view==="runs"&&<button className="primary" disabled={busy||state?.running} onClick={()=>void act(async()=>setState(await request<State>("/check",{})))}>{state?.running?(state.automatic_running||state.scan?.payload.phase==="assessing"?"Assessing High priority jobs...":"Checking boards..."):"Check now"}</button>}</div>}
  {view==='opportunities'&&<CompanyWatchlist company={watchCompany} onClose={()=>setWatchCompany(null)}/>}
  {error&&<p className="error" role="alert">{error}</p>}{connectionError&&<p className="error" role="alert">Connection interrupted. Retrying... {connectionError}</p>}
  {state?.scan_overview&&view==='opportunities'&&<div className="discovery-preferences" role="status">
   <strong>Latest scan · {when(state.scan_overview.started)} · {state.running&&!state.automatic_running?'Running':state.scan_overview.state==='incomplete'?'Finished with limited coverage':statusLabel(state.scan_overview.state)}</strong>
   {state.scan_overview.tracked&&<p>{state.scan_overview.seen} jobs checked · {state.scan_overview.new??'Unknown'} new · {state.scan_overview.high} High priority · {state.scan_overview.worth_checking} Worth checking · {state.scan_overview.requested} deep analysis requests during this scan</p>}
   {(state.pipeline?.running??0)>0&&<p><strong>{state.pipeline?.running} deep analysis in progress now.</strong> <button className="text-action" onClick={()=>onResults('issues')}>View progress</button></p>}
   {(state.pipeline?.queued??0)>0&&<p><strong>{state.pipeline?.queued} High priority jobs queued for automatic analysis.</strong></p>}
   <p>{state.scan_overview.completed} completed recommendations · {state.scan_overview.worth_applying} worth applying. <button className="text-action" onClick={()=>onResults('all')}>View completed analyses</button> <button className="text-action" onClick={()=>onResults('issues')}>Review unfinished or outdated analyses</button></p>
   {state.scan_overview.auto_paused&&<p>Automatic deep analysis is paused. Check Discovery runs → Settings.</p>}
   {state.scan_overview.error&&<p className="error">{state.scan_overview.error}</p>}
   {state.scan_overview.failures.map(f=><p key={f.board} className="error">{state.boards[f.board]||f.board}: {f.reason}</p>)}
  </div>}

  {status.data?.error&&<p className="error" role="alert">{status.data.error}</p>}
  {state&&updating&&<p role="status">Updating saved results…</p>}
  {!state?<p role="status">Preparing your saved jobs…</p>:view==="runs"?<>
   <p><a href="/#usage">View scan costs</a></p>
   <ScanReport scans={state.run_reports??[]} onOpen={onOpen}/>
   <div className="discovery-run" aria-label="Last discovery run">
    <div><span>Last run started</span><strong>{state.scan?when(state.scan.started):"Not run yet"}</strong></div>
    <div><span>Finished</span><strong>{state.scan?.finished?when(state.scan.finished):state.running?"In progress":"—"}</strong></div>
    <div><span>Run status</span><strong>{state.running?(state.automatic_running||state.scan?.payload.phase==="assessing"?"Assessing jobs":"Running"):statusLabel(state.scan?.payload.state??"Not started")}</strong></div>
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
   {state.lenny_connection && <div className="discovery-preferences">
    <strong>Lenny’s Jobs — {({CONNECTED:"Connected",NOT_CONNECTED:"Not connected",LOGIN_REQUIRED:"Sign in again",WAITING_FOR_LOGIN:"Waiting for sign-in",CONNECTING:"Connecting",SCANNING:"Collecting",NEEDS_CHECK:"Check connection",BLOCKED:"Access denied",ERROR:"Connection error",SETUP_REQUIRED:"Setup required"} as Record<string,string>)[state.lenny_connection.state]??state.lenny_connection.state}</strong>
    <p>{state.lenny_connection.message}</p>
    <button disabled={busy||state.lenny_connection.busy} onClick={()=>void act(async()=>{const connection=await request<YCConnection>("/lenny/connect",{});setState({...state,lenny_connection:connection});})}>{state.lenny_connection.state==="CONNECTED"?"Reconnect Lenny’s":"Connect Lenny’s"}</button>
    <button disabled={busy||state.lenny_connection.busy} onClick={()=>void act(async()=>{const connection=await request<YCConnection>("/lenny/check",{});setState({...state,lenny_connection:connection});})}>Check connection</button>
    <button disabled={busy||state.lenny_connection.busy||state.running||state.lenny_connection.state!=="CONNECTED"} onClick={()=>void act(async()=>setState(await request<State>("/lenny/scan",{})))}>Check Lenny’s now</button>
    <details><summary>How sign-in works</summary><p>Connect Lenny’s opens a normal Microsoft Edge window. Sign in there, then close that window so the app can verify Past day access. The app keeps that browser profile in your private workspace and uses it for scheduled checks. If Lenny’s expires the session, reconnect here. No password is requested by this app.</p></details>
   </div>}
   <h2>Job boards</h2><button disabled={busy||state.running} onClick={()=>void act(async()=>setState(await request<State>("/linkedin/scan",{})))}>Check LinkedIn now</button><div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="Job board run summary"><table className="discovery-table board-overview"><thead><tr><th>Board</th><th>Last check</th><th>Status</th><th>Found this run</th><th>Saved</th></tr></thead><tbody>
   {Object.entries(state.boards).map(([id,name])=>{const source=state.scan?.payload.sources?.[id];return <tr key={id}><th scope="row">{name}</th><td>{source?.checked_at?when(source.checked_at):"Not checked this run"}</td><td>{statusLabel(source?.state??(state.running?"Waiting":"Not checked"))}</td><td>{source?.count??"—"}</td><td>{state.board_counts[id]??0}</td></tr>;})}
   </tbody></table></div>
   <SourceLeads/>
   <details className="retrieval-advanced"><summary>Schedule and assessment settings</summary>
    <p>Checks at {state.schedule.join(", ")} using your computer's local clock ({state.timezone}). Keep the local server running and laptop awake. One catch-up check runs after a missed slot when the server is available. Temporary connection failures retry after 10, 30 and 60 seconds. Access denials are not retried during the scan.</p>
    <label className="retrieval-simple-consent"><input type="checkbox" disabled={busy} checked={state.settings.enabled} onChange={e=>{const enabled=e.target.checked;void act(async()=>setState(await request<State>("/settings",{auto_assess:state.settings.auto_assess,enabled})));}}/>Enable local scheduled checks</label>
    <label className="retrieval-simple-consent"><input type="checkbox" disabled={busy} checked={state.settings.auto_assess} onChange={e=>{const auto_assess=e.target.checked;void act(async()=>setState(await request<State>("/settings",{enabled:state.settings.enabled,auto_assess})));}}/>Automatically assess High priority country-matched jobs with Jev as they become eligible (full JD required; TypeSafe usage applies).</label>
    <p><small>Assessment sends job text and retrieved career evidence to TypeSafe. OpenAI creates reusable search embeddings; Jev assesses selected evidence. Posting age does not exclude a High priority role seen in the scan. Failed or interrupted assessments require manual attention.</small></p>
   </details>
   <details className="discovery-notes"><summary>Coverage limits and unavailable boards</summary><p>Counts are PM candidates, not verified matches. Shared jobs can count toward more than one board.</p>
    {Object.entries(state.boards).map(([id,name])=>{const source=state.scan?.payload.sources?.[id];return source&&<p key={id}><strong>{name}:</strong> {source.diagnostic?.reason||source.error||source.coverage}{source.diagnostic && <small> {source.diagnostic.host}{source.diagnostic.path} · {source.diagnostic.attempts} attempts · {source.diagnostic.causes.join(" / ")}</small>}{source.warnings?.length?` ${source.warnings.map(w=>typeof w==="string"?w:`${w.listing}: ${w.reason} (${w.attempts} attempts)`).join("; ")}`:""}</p>;})}
    {Object.entries(state.unavailable).map(([id,reason])=><p key={id}><strong>{id.replaceAll("_"," ")}</strong>: {reason}; not enabled.</p>)}<p>Funding, location eligibility and role suitability still need verification.</p>
   </details>
   {state.scan?.payload.descriptions && <p>{state.scan.payload.descriptions.fetched} descriptions retrieved · {state.scan.payload.descriptions.needs_paste} need a pasted JD</p>}
   {state.scan?.payload.error&&<p role="alert">{state.scan.payload.error}</p>}{state.running&&<p role="status">{state.scan?.payload.phase==="assessing" ? "Collection finished. High priority assessments are being started one at a time." : state.scan?.payload.phase==="classifying" ? "Classifying role responsibilities with Jev." : state.scan?.payload.phase==="descriptions" ? `Retrieving job descriptions: ${state.scan.payload.description_progress?.completed ?? 0} of ${state.scan.payload.description_progress?.total ?? "…"} checked.${state.scan.payload.description_progress?.current ? " Now: " + state.scan.payload.description_progress.current.company + " — " + state.scan.payload.description_progress.current.title : ""}` : state.scan?.payload.phase==="company_leads" ? "Job boards checked. Fetching company lead sources." : "Checking sources. Results appear in New opportunities as each board finishes."}</p>}
  </>:<>
   {state.keyword_profile && <details className="discovery-preferences"><summary>Your preferences</summary>
    <p><strong>Location:</strong> {state.location_preference?.label || "Preferred country"}</p>
    <p><strong>Company focus:</strong> {companyFocus}{companyFocus==='startups'&&` · ${state.company_preference?.preferred_stages.join(' → ')}`}. Edit in Your context → Your job preferences.</p>
    <p><strong>Target roles:</strong> {state.keyword_profile.target.roles.join(", ")}</p>
    <p><strong>Target keywords:</strong> {state.keyword_profile.target.keywords.join(", ") || "None added"}</p>
    {!editingTarget ? <button onClick={()=>{setTargetRoles(state.keyword_profile!.target.roles.join("\n"));setTargetKeywords(state.keyword_profile!.target.keywords.join(", "));setTargetVersion(state.keyword_profile!.target.version);setEditingTarget(true);}}>Edit targets</button> : <form onSubmit={e=>{e.preventDefault();void act(async()=>{setState(await request<State>("/target",{roles:targetRoles.split("\n").map(s=>s.trim()).filter(Boolean),keywords:targetKeywords.split(",").map(s=>s.trim()).filter(Boolean),expected_version:targetVersion}));setEditingTarget(false);});}}>
     <label>Target roles (one per line)<textarea value={targetRoles} onChange={e=>setTargetRoles(e.target.value)} required/></label>
     <label>Target keywords (comma separated)<textarea value={targetKeywords} onChange={e=>setTargetKeywords(e.target.value)}/></label>
     <button disabled={busy} type="submit">Save targets</button> <button disabled={busy} type="button" onClick={()=>setEditingTarget(false)}>Cancel</button>
    </form>}

   </details>}
   <p>Initial matches help you prioritize. Select any job for deep analysis, including low-priority or unassessed jobs.</p>
   <div className="view-tabs" role="tablist" aria-label="New jobs views"><button role="tab" aria-selected={tab==="jobs"} className={tab==="jobs"?"active":""} onClick={()=>{setTab("jobs");setPage(1);}}>Jobs</button><button role="tab" aria-selected={tab==="help"} className={tab==="help"?"active":""} onClick={()=>{setTab("help");setPage(1);}}>Needs your help{state.help_count?` (${state.help_count})`:""}</button></div>
   {tab==="jobs"&&<>
   <div className="opportunity-filters discovery-filters">
    <label>Search<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Company, role or location"/></label>
    <label>Priority<select value={priorityFilter} onChange={e=>setPriorityFilter(e.target.value)}><option value="all">All priorities</option><option value="worth_checking">Worth checking</option><option value="high">High priority</option><option value="not_screened">Not screened</option><option value="low">Low priority</option></select></label>
    <label>Companies<select value={companyFilter} onChange={e=>setCompanyFilter(e.target.value)}><option value="focus">{companyFocus==='startups'?'Your startup focus':companyFocus==='established'?'Established companies':'All companies'}</option><option value="all">All companies</option><option value="preferred">Preferred stages</option><option value="startups">All reported startups</option><option value="established">Established companies</option><option value="unknown">Company / stage unknown</option></select></label>
    <label>Location<select value={locationFilter} onChange={e=>setLocationFilter(e.target.value)}><option value="match">{state.location_preference?.label || "Preferred country"} matches</option><option value="unclear">Location unclear</option><option value="all">All locations</option></select></label>
    <label>Job board<select value={boardFilter} onChange={e=>setBoardFilter(e.target.value)}><option value="all">All boards</option>{Object.entries(state.boards).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
    <label>Show<select value={filter} onChange={e=>setFilter(e.target.value)}>{[["active","Active jobs (4 days + kept / date unknown)"],["passed","Passed by me"],["kept","Kept jobs"],["watched","Watched companies"],["recent","Posted <24h (source-reported)"],["older","Older than 24h"],["uncertain","Posting date uncertain"],["all","All saved"],["discovered","Discovered <24h (any posting age)"],["missing","Description needed"]].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
   </div>
   {companyFilter==='focus'&&companyFocus!=='any'&&<p className="discovery-clock">Showing your company focus. <button className="text-action" onClick={()=>setCompanyFilter('unknown')}>Review unknown companies</button> · <button className="text-action" onClick={()=>setCompanyFilter('all')}>Show all companies</button></p>}
   <div className="opportunity-toolbar">
    <div className="opportunity-count"><strong>{total} roles</strong><span>{Object.keys(selected).length?`${Object.keys(selected).length} selected across all pages`:'Select roles to assess'}</span></div>
    <div className="selection-actions">{Object.keys(selected).length>0&&<button className="text-action" disabled={busy} onClick={()=>setSelected({})}>Clear</button>}<button className="primary" disabled={busy||!Object.keys(selected).length} onClick={()=>void act(()=>analyzeSelected())}>Analyze deeply{Object.keys(selected).length?` (${Object.keys(selected).length})`:''}</button><button className="text-action" onClick={()=>onResults()}>Analyzed jobs →</button></div>
   </div>
   {selectionNotice&&<p role="status">{selectionNotice}</p>}
   <div className="opportunity-pagination"><small>{total?`${(currentPage-1)*25+1}–${Math.min(currentPage*25,total)} of ${total}`:'No matching roles'} · API usage applies when analyzing</small><div><button disabled={listing.isPlaceholderData||currentPage===1} onClick={()=>setPage(currentPage-1)}>Previous</button><span>Page {currentPage} of {pages}</span><button disabled={listing.isPlaceholderData||currentPage===pages} onClick={()=>setPage(currentPage+1)}>Next</button></div></div>
   {state.freshness_counts&&<p className="muted">For these preferences: <button className="text-button" onClick={()=>setFilter("recent")}>{state.freshness_counts.recent} posted &lt;24h</button> · <button className="text-button" onClick={()=>setFilter("older")}>{state.freshness_counts.older} older</button> · <button className="text-button" onClick={()=>setFilter("uncertain")}>{state.freshness_counts.uncertain} date uncertain</button></p>}
   {!total?<p>{state.scan?"No jobs match these filters. Check older jobs or uncertain posting dates above, or broaden the company and location filters.":"Open Discovery runs and choose Check now to collect listings."}</p>:<div className="discovery-table-scroll" tabIndex={0} role="region" aria-label="All opportunities"><table className="discovery-table opportunities-table"><thead><tr><th scope="col">Company / role</th><th scope="col">Priority / skills</th><th scope="col">Link</th><th scope="col">Job board</th><th scope="col">Location</th><th scope="col">Posted / freshness</th></tr></thead><tbody>
    {pageRows.map(item=><tr key={item.id}><td>{<label className="select-role"><input type="checkbox" aria-label={`Select ${item.company} · ${item.title} for deep analysis`} disabled={busy||!selectable(item)||(!selected[item.id]&&Object.keys(selected).length>=100)} checked={!!selected[item.id]} onChange={e=>{const checked=e.target.checked;setSelectionNotice('');setSelected(current=>{const next={...current};if(checked)next[item.id]=item.content_hash;else delete next[item.id];return next;});}}/>Select</label>}<strong>{item.company}</strong>{item.company_fit&&<small title={item.company_fit.reason}>{item.company_fit.label}{item.company_fit.backing_reported?" · Backing reported":""}</small>}<div className="discovery-role">{item.title}</div>{item.role_classification && <small title={`Jev · ${item.role_classification.model} · advisory classification`}>{Object.values(item.role_classification.labels).join(" · ")}</small>}
     <div className="row-actions">
      {(item.needs_help||[]).length>0&&<button className="text-action" onClick={()=>{setTab("help");setPage(1);}}>Needs your help →</button>}
      {rowNotice[item.id]&&<strong role="status">{rowNotice[item.id]}</strong>}
      {item.description_length<200 && item.description_fetch?.state==="NEEDS_PASTE" && <small>Open the listing and paste its full JD.</small>}
      {item.selected_assessment?.state==="QUEUED"&&<strong role="status">Queued for deep analysis{item.selected_assessment.position?` · #${item.selected_assessment.position} in line`:''}</strong>}
      {item.selected_assessment?.state==="RUNNING"&&item.assessment?.state!=="RUNNING"&&<strong role="status">Starting analysis</strong>}
      {item.selected_assessment?.state==="NEEDS_ATTENTION"&&item.selected_assessment.error&&<small role="status">{item.selected_assessment.error}</small>}
      {item.assessment?.state==="QUEUED"&&!item.selected_assessment&&<strong role="status">Queued for deep analysis</strong>}
      {item.queued_for_assessment && <strong role="status">Queued for automatic analysis</strong>}
      {item.assessment?.state==="RUNNING" && <strong role="status">Analyzing</strong>}
      {["ERROR","INTERRUPTED","NEEDS_ATTENTION","PAUSED"].includes(item.assessment?.state??"") && !(item.needs_help||[]).length && <><strong>Deep analysis stopped</strong>{item.assessment?.error&&<small>{item.assessment.error}</small>}</>}
      {item.assessment?.state==="STALE" && <strong>Outdated analysis{item.assessment.score!==null?` · ${item.assessment.score}/100 before`:''}</strong>}
      {["ERROR","INTERRUPTED","PAUSED","STALE"].includes(item.assessment?.state??"") && !(item.needs_help||[]).some(h=>h.kind==="replace_jd") && !queueActive(item) && item.description_length>=200 && !item.description_changed && <button className="primary" disabled={!!rowBusy[item.id]} onClick={()=>void analyzeRow(item)}>{item.assessment?.state==="STALE"?"Re-run analysis":"Retry deep analysis"} · API usage</button>}
      {(["ERROR","INTERRUPTED","NEEDS_ATTENTION"].includes(item.assessment?.state??"") || (item.assessment?.state==="STALE" && item.assessment.score===null)) && <button onClick={()=>onResults("issues")}>Review analysis issue</button>}
      {item.description_length<200 || item.description_changed ? <button disabled={busy} onClick={()=>void act(()=>openJD(item))}>{item.description_changed ? "Review updated JD" : "Get full JD"}</button> : <>
       {!queueActive(item) && !item.queued_for_assessment && ["NOT_STARTED", ""].includes(item.assessment?.state ?? "") && <button className="primary" disabled={!!rowBusy[item.id]} onClick={()=>void analyzeRow(item)}>{item.job_id ? "Analyze deeply again" : "Analyze deeply"} · API usage</button>}
      </>}
      {item.job_id && item.assessment?.state==="NOT_STARTED" && <button disabled={busy} onClick={()=>void act(()=>onOpen(item.job_id!))}>Open role &amp; analysis</button>}
      {item.passed?<button disabled={busy} onClick={()=>void act(()=>passRow(item,false))}>Undo pass</button>:!queueActive(item)&&item.assessment?.state!=="QUEUED"&&item.assessment?.state!=="RUNNING"&&<button disabled={busy} title="Not interested: hide from New jobs and skip automatic analysis" onClick={()=>void act(()=>passRow(item,true))}>Pass</button>}
      {item.description_length>=200&&!item.description_changed&&<RowWritingHelp oid={item.id} jobId={item.job_id||null} company={item.company} url={item.url} yc={isYCListing(item.url)||(item.listing_urls??[]).some(isYCListing)||item.sources.some(s=>s.board==='yc')} hasDescription/>}
     </div>
     <JobDetails item={item} boards={state.boards}/><MarkApplied jobId={item.job_id||undefined} opportunityId={item.id} company={item.company}/><div><button className="text-action" disabled={busy} onClick={()=>void act(async()=>{await request(`/${item.id}/keep`,{keep:!item.kept});setState();})}>{item.kept?"Unkeep job":"Keep job"}</button> · <button className="text-action" onClick={()=>setWatchCompany(item.company)}>{item.company_watched?"Watching company":"Watch company"}</button></div></td>
     <td>{item.sponsorship&&<div className="sponsorship-blocked"><strong title={item.sponsorship.quote}>No visa sponsorship · not auto-analyzed</strong><small>“{item.sponsorship.quote}”</small></div>}{item.keyword_match && <div className="opportunity-priority"><span className={`preference-badge preference-${item.keyword_match.priority}`} title={item.keyword_match.reason}>{item.keyword_match.priority==='low'?'Low priority':item.keyword_match.label}</span>{item.keyword_match.reason&&<details><summary>Why this priority</summary><p>{item.keyword_match.reason}</p></details>}</div>}{item.keyword_match?.experience.length ? <details className="keyword-details"><summary>Matched skills ({item.keyword_match.experience.length})</summary><div className="keyword-tags">{item.keyword_match.experience.map(match=><span key={match.term}>{match.term}</span>)}</div></details> : null}</td>
     <td><a href={item.url} target="_blank" rel="noreferrer">View listing ↗</a></td>
     <td>{[...new Set(item.sources.map(s=>state.boards[s.board]||s.board))].join(", ")}</td><td>{item.location||"Unknown"}</td>
     <td><span className="discovery-status">{freshnessLabel(item)}</span><small>{item.posted_at?(/^\d{4}-\d{2}-\d{2}$/.test(item.posted_at)?item.posted_at:when(item.posted_at)):"Not provided"}</small>{item.date_label&&<small>{item.date_label}</small>}<small>Discovered: {when(item.first_seen)}</small></td>
    </tr>)}
   </tbody></table></div>}
   </>}
   {tab==="help"&&<>
    <p className="muted">Each job here needs one thing from you before it can be screened or analyzed. A job leaves this list as soon as it's fixed or passed.</p>
    {!pageRows.length?<p>Nothing needs your help right now.</p>:<div className="discovery-table-scroll"><table className="discovery-table help-table" aria-label="Jobs that need your help"><thead><tr><th scope="col">Job</th><th scope="col">What's wrong</th><th scope="col">What to do</th><th scope="col">Action</th></tr></thead><tbody>
     {pageRows.map(item=>{const h=(item.needs_help||[])[0];if(!h)return null;return <tr key={item.id}>
      <td><strong>{item.company}</strong><div>{item.title}</div><small>{item.location||"Location unknown"}</small><div><a href={item.url} target="_blank" rel="noreferrer">View listing ↗</a></div></td>
      <td>{h.issue||h.message}</td><td>{h.todo||""}</td>
      <td><div className="row-actions">{rowNotice[item.id]&&<small role="status">{rowNotice[item.id]}</small>}
       {["paste_jd","replace_jd","review_jd"].includes(h.kind)&&<button className="primary" disabled={busy} onClick={()=>void act(()=>openJD(item))}>{h.kind==="review_jd"?"Review description":"Paste description"}</button>}
       {h.kind==="retry"&&<button className="primary" disabled={!!rowBusy[item.id]} onClick={()=>void analyzeRow(item)}>Retry analysis · API usage</button>}
       {h.kind==="review_analysis"&&item.job_id&&<button className="primary" disabled={busy} onClick={()=>void act(()=>onOpen(item.job_id!))}>Open role</button>}
       <button disabled={busy} title="Not interested: hide this job" onClick={()=>void act(()=>passRow(item,true))}>Pass</button>
      </div></td></tr>;})}
    </tbody></table></div>}
    {pages>1&&<div className="opportunity-pagination"><small>Page {currentPage} of {pages}</small><div><button disabled={currentPage===1} onClick={()=>setPage(currentPage-1)}>Previous</button><button disabled={currentPage===pages} onClick={()=>setPage(currentPage+1)}>Next</button></div></div>}
   </>}
  </>}
 <dialog ref={jdDialog} className="experience-dialog" aria-label="Review full job description" onCancel={e=>{if(busy)e.preventDefault();else setJD(null);}}>
  {jd && <form onSubmit={e=>{e.preventDefault();void act(async()=>{await request(`/${jd.id}/description`,{expected_hash:jd.expected_hash,title:jd.title,company:jd.company,description:jd.description,extraction_id:jd.extraction_id});setSaved(v=>({...v,[jd.id]:jd.expected_hash}));setRowNotice(n=>({...n,[jd.id]:'Description saved. The list updates within about 30 seconds.'}));setState();setJD(null);});}}>
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

function JobDetails({item,boards}:{item:Item;boards:Record<string,string>}) {
 const [open,setOpen]=useState(false);
 const detail=useQuery({queryKey:['job-detail',item.id,item.content_hash],queryFn:({signal})=>request<{description:string;company_fit?:CompanyFit}>(`/${item.id}/detail`,undefined,signal),enabled:open});
 return <details className="discovery-row-details" onToggle={e=>setOpen(e.currentTarget.open)}><summary>Job details</summary>
 {open&&<><p>{item.company_fit?.reason}</p>{detail.data?.company_fit?.evidence?.map((e,i)=><p key={i}><small>{e.quote} {e.url&&<a href={e.url} target="_blank" rel="noreferrer">Source</a>}</small></p>)}{(item.listing_urls?.length||0)>1&&<p>Same role listed at {item.listing_urls!.length} source URLs: {item.listing_urls!.map((url,i)=><a key={url} href={url} target="_blank" rel="noreferrer">{i>0?' · ':''}Listing {i+1}</a>)}</p>}<p>Funding: {item.funding||'Unknown'}</p><p>First detected: {when(item.first_seen)}<br/>Last seen: {when(item.last_seen)}<br/>Sources: {item.sources.map(s=>boards[s.board]||s.board).join(', ')}</p>
 {item.description_changed&&<p className="notice">Description changed after import. Existing analysis has not been overwritten.</p>}
 {detail.isPending?<p>Loading description…</p>:detail.error?<p role="alert">{detail.error.message} <button onClick={()=>void detail.refetch()}>Retry</button></p>:detail.data?.description?<pre className="retrieval-source">{detail.data.description}</pre>:<p>Full description needed. This board supplied metadata only.</p>}</>}
 </details>;
}
