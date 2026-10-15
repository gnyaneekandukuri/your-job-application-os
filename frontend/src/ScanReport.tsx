import {summarizeScan,assessmentLabel,type ScanRole} from './scanReportLogic';
const scanLabel=(state:string)=>({incomplete:"Finished · partial coverage",complete:"Finished",interrupted:"Stopped before finishing",running:"Running",error:"Stopped with an error",failed:"No boards returned results"}[state]??state.replaceAll("_"," "));
const boardNames:Record<string,string>={yc:'Y Combinator',builtin:'Built In',a16z:'a16z',sequoia:'Sequoia',lightspeed:'Lightspeed',linkedin:'LinkedIn',hn:"HN Who’s Hiring",lenny:"Lenny’s Jobs",cosign:'Cosign'};
export type ScanReportData={id:number;started:string;finished:string|null;state:string;new_count?:number|null;tracked:boolean;error?:string;sources:Record<string,{state:string;count?:number;coverage?:string;error?:string;warnings?:(string|{reason?:string})[]}>;roles:ScanRole[]};
export function ScanReport({scans,onOpen}:{scans:ScanReportData[];onOpen:(id:string)=>Promise<void>}) {
 return <section className="scan-report" aria-label="Scan reports"><h2>What happened in each scan</h2>
 {!scans.length&&<p>No scans yet.</p>}
 {scans.map((scan,index)=>{const s=summarizeScan(scan.roles);return <details key={scan.id} open={index===0}><summary>{new Date(scan.started).toLocaleString()} · {scanLabel(scan.state)}</summary>
 <p>{scan.state==='incomplete'?'Collection finished with limited board coverage; this was not a complete search of every board.':scan.state==='running'?'This scan is still running. Counts are provisional.':'Results recorded during this scan.'} {scan.error}</p>
 {!scan.tracked?<p>Detailed movement was not recorded for this older scan.</p>:<>
 <h3>What came in?</h3>
 <p><strong>{s.total} job records checked</strong> · {s.newRecorded} marked new · {s.repeat} previously seen{s.unknownNew>0&&` · ${s.unknownNew} with new/repeat status unavailable`}</p>
 <p>These are saved listing records, not necessarily distinct openings: the same opening can appear on several boards or in several locations. Repeat sightings are not new jobs.</p>
 {scan.new_count!=null&&scan.new_count!==s.newRecorded&&<p role="status">Collection reported {scan.new_count} new listings, but detailed records mark {s.newRecorded} new. The counts do not reconcile; do not treat the collection total as a verified unique-job count.</p>}
 <h3>What did initial screening decide?</h3>
 <p><strong>{s.high} High priority</strong> · {s.worth} Worth checking · {s.low} Low priority · {s.needs} Need information · {s.unscreened} Not screened</p>
 <p>Needs information and Not screened are unresolved, not rejections. High priority includes previously seen roles.</p>
 {s.low>0&&<details><summary>Why were {s.low} roles Low priority?</summary><ul>{Object.entries(s.lowReasons).map(([reason,n])=><li key={reason}>{n} — {reason}</li>)}</ul></details>}
 <h3>Did all high-priority roles get analyzed?</h3>
 <p>{s.requested} deep analyses requested in this scan · {s.completedRequests} of those completed. Analysis completion does not depend on a recommendation being ready.</p>
 <ul>{Object.entries(s.highStates).map(([state,n])=><li key={state}><strong>{n}</strong> — {({COMPLETE:'completed analysis available',STALE:'older analysis marked outdated',NOT_STARTED:'analysis not started',JD_CHANGED:'job description changed',RUNNING:'analysis still running'} as Record<string,string>)[state]??state.replaceAll('_',' ').toLowerCase()}</li>)}</ul>
 <p>These statuses account for all {s.high} High-priority records at the last update of this scan. They are not all new analyses. Current status is in Your jobs.</p>
 <details><summary>See high-priority roles and why analysis did not start</summary><ul>{scan.roles.filter(r=>r.priority==='High priority').map(r=><li key={r.id}><strong>{r.company} · {r.title}</strong> — {assessmentLabel(r)}. {!r.assessment_requested&&r.assessment?.state!=='COMPLETE'&&(r.automatic_skip_reason||r.error||'The specific skip reason was not recorded for this scan.')}{r.job_id&&<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>}</li>)}</ul></details>
 </>}
 <h3>Which boards were checked?</h3>
 <p>Board counts are sightings before cross-board deduplication. They include repeats and should not be added up as new jobs.</p>
 <div className="discovery-table-scroll"><table className="discovery-table"><thead><tr><th>Board</th><th>Listings returned</th><th>Coverage and issues</th></tr></thead><tbody>{Object.entries(scan.sources).map(([board,source])=><tr key={board}><td>{boardNames[board]??board}</td><td>{source.count??'Unavailable'}</td><td><strong>{source.state.replaceAll('_',' ')}</strong><p>{source.coverage}</p>{source.error&&<p>{source.error}</p>}{source.warnings?.map((w,i)=><p key={i}>{typeof w==='string'?w:w.reason}</p>)}</td></tr>)}</tbody></table></div>
 {scan.tracked&&<details><summary>All {s.total} job records</summary><div className="discovery-table-scroll"><table className="discovery-table"><thead><tr><th>Role</th><th>New or repeat</th><th>Screening</th><th>Deep analysis</th><th>Details</th></tr></thead><tbody>{scan.roles.map(r=><tr key={r.id}><td>{r.company}<br/>{r.title}</td><td>{r.newly_saved===undefined?'Unknown':r.newly_saved?'New':'Previously seen'}</td><td>{r.priority??'Not reached'}</td><td>{assessmentLabel(r)}</td><td>{r.error||r.automatic_skip_reason||r.reason||r.stage}{r.job_id&&<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>}</td></tr>)}</tbody></table></div></details>}
 </details>})}
 </section>;
}
