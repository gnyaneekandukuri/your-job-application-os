export type ScanRole={id:string;source_boards?:string[];company:string;title:string;newly_saved?:boolean;priority?:string;reason?:string;stage:string;assessment_requested?:boolean;automatic_skip_reason?:string;job_id?:string;error?:string;assessment?:{state:string;recommendation?:string|null}};
export function summarizeScan(roles:ScanRole[]){
 const count=(test:(r:ScanRole)=>boolean)=>roles.filter(test).length;
 const high=roles.filter(r=>r.priority==='High priority');
 const states:Record<string,number>={};
 for(const r of high){const s=r.assessment?.state??'NOT_STARTED';states[s]=(states[s]??0)+1;}
 const lowReasons:Record<string,number>={};
 for(const r of roles.filter(r=>r.priority==='Low priority')){const reason=r.reason||'Reason not recorded';lowReasons[reason]=(lowReasons[reason]??0)+1;}
 return {total:roles.length,newRecorded:count(r=>r.newly_saved===true),repeat:count(r=>r.newly_saved===false),unknownNew:count(r=>r.newly_saved===undefined),
 high:high.length,worth:count(r=>r.priority==='Worth checking'),low:count(r=>r.priority==='Low priority'),needs:count(r=>r.priority==='Needs information'),unscreened:count(r=>!r.priority||r.priority==='Not screened'),
 requested:count(r=>!!r.assessment_requested),completedRequests:count(r=>!!r.assessment_requested&&r.assessment?.state==='COMPLETE'),highStates:states,lowReasons};
}
export function assessmentLabel(r:ScanRole){
 const state=r.assessment?.state??'NOT_STARTED';
 if(r.assessment_requested)return ({COMPLETE:'Completed in this scan',RUNNING:'Still analyzing',STALE:'Requested; result now outdated',NOT_STARTED:'Requested; not started'}[state]??`Requested · ${state.replaceAll('_',' ').toLowerCase()}`);
 return ({COMPLETE:'Existing completed analysis',STALE:'Older analysis marked outdated',JD_CHANGED:'Job description changed',NOT_STARTED:'Not started',RUNNING:'Analysis in progress'}[state]??state.replaceAll('_',' ').toLowerCase());
}

export function needsHelp(r:ScanRole){
 return !!r.error||r.priority==='Needs information'||r.priority==='Not screened'||!r.priority||['ERROR','FAILED','INTERRUPTED','NEEDS_ATTENTION','NEEDS_JD','JD_CHANGED'].includes(r.assessment?.state??'');
}
export function boardRoles(roles:ScanRole[],board:string){return roles.filter(r=>r.source_boards?.includes(board));}
