import {summarizeScan,assessmentLabel,needsHelp,boardRoles,type ScanRole} from './scanReportLogic';
const scanLabel=(state:string)=>({incomplete:"Finished · partial coverage",complete:"Finished",interrupted:"Stopped before finishing",running:"Running",error:"Stopped with an error",failed:"No boards returned results"}[state]??state.replaceAll("_"," "));
const boardNames:Record<string,string>={yc:'Y Combinator',builtin:'Built In',a16z:'a16z',sequoia:'Sequoia',lightspeed:'Lightspeed',linkedin:'LinkedIn',hn:"HN Who’s Hiring",lenny:"Lenny’s Jobs",cosign:'Cosign'};
export type ScanReportData={id:number;started:string;finished:string|null;state:string;new_count?:number|null;tracked:boolean;board_tracking?:boolean;error?:string;sources:Record<string,{state:string;count?:number;coverage?:string;error?:string;warnings?:(string|{reason?:string})[]}>;roles:ScanRole[]};
export function ScanReport({scans,onOpen}:{scans:ScanReportData[];onOpen:(id:string)=>Promise<void>}) {
 return <section className="scan-report" aria-label="Scan reports"><h2>What happened in each scan</h2>
 {!scans.length&&<p>No scans yet.</p>}
 {scans.map((scan,index)=>{const s=summarizeScan(scan.roles);return <details key={scan.id} open={index===0}><summary>{new Date(scan.started).toLocaleString()} · {scanLabel(scan.state)}</summary>
 <p>{scan.state==='incomplete'?'Collection finished with limited board coverage; this was not a complete search of every board.':scan.state==='running'?'This scan is still running. Counts are provisional.':'Results recorded during this scan.'} {scan.error}</p>
 {!scan.tracked?<p>Detailed movement was not recorded for this older scan.</p>:<>
 <h3>1. Scan total</h3>
 <p><strong>{s.total} job records checked</strong> · {s.newRecorded} marked new · {s.repeat} previously seen{s.unknownNew>0&&` · ${s.unknownNew} with new/repeat status unavailable`}</p>
 <p>These are saved listing records, not necessarily distinct openings: the same opening can appear on several boards or in several locations. Repeat sightings are not new jobs.</p>
 {scan.new_count!=null&&scan.new_count!==s.newRecorded&&<p role="status">Collection reported {scan.new_count} new listings, but detailed records mark {s.newRecorded} new. The counts do not reconcile; do not treat the collection total as a verified unique-job count.</p>}
 <h3>2. Initial analysis</h3>
 <p><strong>{s.high} High priority</strong> · {s.worth} Worth checking · {s.low} Low priority · {s.needs} Need information · {s.unscreened} Not screened</p>
 <p>Needs information and Not screened are unresolved, not rejections. High priority includes previously seen roles.</p>
 {s.low>0&&<details><summary>Why were {s.low} roles Low priority?</summary><ul>{Object.entries(s.lowReasons).map(([reason,n])=><li key={reason}>{n} — {reason}</li>)}</ul></details>}
 <h3>3. Deep analysis</h3>
 <p>{s.requested} deep analyses requested in this scan · {s.completedRequests} of those completed. Analysis completion does not depend on a recommendation being ready.</p>
 <ul>{Object.entries(s.highStates).map(([state,n])=><li key={state}><strong>{n}</strong> — {({COMPLETE:'completed analysis available',STALE:'older analysis marked outdated',NOT_STARTED:'analysis not started',JD_CHANGED:'job description changed',RUNNING:'analysis still running'} as Record<string,string>)[state]??state.replaceAll('_',' ').toLowerCase()}</li>)}</ul>
 <p>These statuses account for all {s.high} High-priority records at the last update of this scan. They are not all new analyses. Current status is in Your jobs.</p>
 <details><summary>See high-priority roles and why analysis did not start</summary><ul>{scan.roles.filter(r=>r.priority==='High priority').map(r=><li key={r.id}><strong>{r.company} · {r.title}</strong> — {assessmentLabel(r)}. {!r.assessment_requested&&r.assessment?.state!=='COMPLETE'&&(r.automatic_skip_reason||r.error||'The specific skip reason was not recorded for this scan.')}{r.job_id&&<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>}</li>)}</ul></details>
 </>}
 <h3>Needs help</h3>
 <HelpRows roles={scan.roles} onOpen={onOpen}/>
 <h3>Breakdown by job board</h3>
 <p>Board counts are sightings before cross-board deduplication. They include repeats and should not be added up as new jobs.</p>
 {Object.entries(scan.sources).map(([board,source])=>{const roles=boardRoles(scan.roles,board);const b=summarizeScan(roles);return <details key={board}><summary>{boardNames[board]??board} · {source.count??'Unknown'} listings returned · {source.state.replaceAll('_',' ')}</summary>
 <p>{source.coverage}</p>{source.error&&<p role="alert">{source.error}</p>}{source.warnings?.map((w,i)=><p key={i}>{typeof w==='string'?w:w.reason}</p>)}
 {!scan.board_tracking?<p>Per-board analysis tracking was not recorded for this older scan. Its board collection counts are available above; analysis totals are available for the whole scan.</p>:<>
 <h4>1. Scan total</h4><p>{source.count??'Unknown'} returned · {b.total} tracked job records · {b.newRecorded} newly discovered · {b.repeat} previously seen{b.unknownNew>0&&` · ${b.unknownNew} unknown`}</p>
 <h4>2. Initial analysis</h4><p>{b.high} High priority · {b.worth} Worth checking · {b.low} Low priority · {b.needs} Need information · {b.unscreened} Not screened</p>
 {b.low>0&&<details><summary>Why Low priority?</summary><ul>{Object.entries(b.lowReasons).map(([why,n])=><li key={why}>{n} — {why}</li>)}</ul></details>}
 <h4>3. Deep analysis</h4><p>{b.requested} requested in this scan · {b.completedRequests} of those completed</p>
 <p>High-priority records: {Object.entries(b.highStates).map(([state,n])=>`${n} ${state.replaceAll('_',' ').toLowerCase()}`).join(' · ')||'None'}</p>
 <h4>Needs help</h4><HelpRows roles={roles} onOpen={onOpen}/>
 <details><summary>See {b.total} roles from this board</summary><ul>{roles.map(r=><li key={r.id}>{r.company} · {r.title} — {r.priority??'Not screened'} — {assessmentLabel(r)}{r.automatic_skip_reason&&` · ${r.automatic_skip_reason}`}{r.job_id&&<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>}</li>)}</ul></details>
 </>}
 </details>})}
 <small>A role found on multiple boards appears under each board, but is analyzed only once through the duplicate-handling rules. Board analysis counts overlap; use the combined scan totals above.</small>
 {scan.tracked&&<details><summary>All {s.total} job records</summary><div className="discovery-table-scroll"><table className="discovery-table"><thead><tr><th>Role</th><th>New or repeat</th><th>Screening</th><th>Deep analysis</th><th>Details</th></tr></thead><tbody>{scan.roles.map(r=><tr key={r.id}><td>{r.company}<br/>{r.title}</td><td>{r.newly_saved===undefined?'Unknown':r.newly_saved?'New':'Previously seen'}</td><td>{r.priority??'Not reached'}</td><td>{assessmentLabel(r)}</td><td>{r.error||r.automatic_skip_reason||r.reason||r.stage}{r.job_id&&<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>}</td></tr>)}</tbody></table></div></details>}
 </details>})}
 </section>;
}

function HelpRows({roles,onOpen}:{roles:ScanRole[];onOpen:(id:string)=>Promise<void>}){
 const attention=roles.filter(needsHelp);
 return <>{!attention.length?<p>No job-level issues recorded.</p>:<details><summary>{attention.length} roles need information, screening, or analysis review</summary><ul>{attention.map(r=><li key={r.id}><strong>{r.company} · {r.title}</strong> — {r.error||r.automatic_skip_reason||r.reason||assessmentLabel(r)}{r.job_id?<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>:<span> — Find this role in New jobs to review its JD or request analysis.</span>}</li>)}</ul></details>}<small>Low priority alone is not an error. Unscreened jobs need processing, not necessarily input from you.</small></>;
}
