const scanLabel=(state:string)=>({incomplete:"Finished · partial coverage",complete:"Finished",interrupted:"Stopped before finishing",running:"Running",error:"Stopped with an error",failed:"No boards returned results"}[state]??state.replaceAll("_"," "));
type ScanRole={id:string;company:string;title:string;priority?:string;reason?:string;stage:string;assessment_requested?:boolean;job_id?:string;error?:string;assessment?:{state:string;recommendation?:string|null}};
export type ScanReportData={id:number;started:string;finished:string|null;state:string;new_count?:number|null;tracked:boolean;error?:string;sources:Record<string,{state:string;count?:number}>;roles:ScanRole[]};
export function ScanReport({scans,onOpen}:{scans:ScanReportData[];onOpen:(id:string)=>Promise<void>}) {
 return <details className="scan-report"><summary>Scan activity · {scans[0]?`${new Date(scans[0].started).toLocaleString()} · ${scanLabel(scans[0].state)}`:'No scans yet'}</summary>
 {scans.map(scan=><details key={scan.id}><summary>{new Date(scan.started).toLocaleString()} · {scanLabel(scan.state)} · {scan.new_count??'Unknown'} new listings</summary>
 <p>{scan.state==='interrupted'?'This scan did not finish. Saved work remains available.':scan.state==='incomplete'?'The run ended with incomplete board coverage.':'Status reflects this run only.'} {scan.error}</p>
 <p>{Object.entries(scan.sources).map(([board,source])=>`${board}: ${source.state} (${source.count??'—'} found)`).join(' · ')}</p>
 {!scan.tracked?<p>Detailed movement was not recorded for this older scan. Current assessments are shown in the queues below.</p>:<>
 <p>{scan.roles.length} unique listings seen · {scan.roles.filter(r=>r.priority==='High priority').length} high priority · {scan.roles.filter(r=>r.priority==='Worth checking').length} worth checking · {scan.roles.filter(r=>!r.priority||r.priority==='Not screened').length} not screened · {scan.roles.filter(r=>r.assessment_requested).length} assessment requests · {scan.roles.filter(r=>r.assessment_requested&&r.assessment?.state==='COMPLETE'&&r.assessment.recommendation).length} requests completed</p>
 <small>Last recorded state during this scan. Current status is in your decision queue.</small>
 <div className="discovery-table-scroll"><table className="discovery-table"><thead><tr><th>Role</th><th>Screening</th><th>Deep assessment</th><th>Details</th></tr></thead><tbody>{scan.roles.map(r=><tr key={r.id}><td>{r.company}<br/>{r.title}</td><td>{r.priority??'Not reached'}</td><td>{r.assessment_requested?(r.assessment?.state??r.stage):r.assessment?.state==='COMPLETE'?'Existing assessment':'Not requested'}</td><td>{r.error||r.reason||r.stage}{r.job_id&&<button onClick={()=>void onOpen(r.job_id!)}>Open role</button>}</td></tr>)}</tbody></table></div>
 </>}
 </details>)}
 </details>;
}
