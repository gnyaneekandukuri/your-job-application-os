export type Explanation={summary:string;strengths:{requirement_id:string;requirement:string;alignment:string;tentative:boolean;citations:{source_title:string;source_path:string;start_line:number;quote:string}[]}[];gaps:{requirement_id:string;requirement:string;detail:string;tentative:boolean}[];decision_basis:{label:string;passed:boolean;detail:string}[]};
export type Assessment = {assessed_at?:string|null;explanation?:Explanation;state:string;score:number|null;provisional?:boolean;listing_changed?:boolean;recommendation:string|null;rationale?:string;choice?:string|null;stage?:string;error?:string;recommendation_error?:string};
export type Result = {last_scan_id?:number|null;last_scanned_at?:string|null;job_id:string;opportunity_id?:string|null;company:string;title:string;url:string;assessment:Assessment};
export function queueGroup(r:Result):string {
 const a=r.assessment;
 if(a.state==='RUNNING')return 'running';
 if(a.state!=='COMPLETE'||!a.recommendation)return 'attention';
 if(a.choice==='SKIP')return 'skipped';
 if(a.listing_changed)return 'decision';
 if(a.choice==='CASUAL'||a.choice==='ATTACK_HARD')return 'shortlist';
 return 'decision';
}
export function nextAction(r:Result):string {
 const a=r.assessment;
 if(a.state==='STALE')return 'Review outdated assessment';
 if(a.state==='RUNNING')return 'View progress';
 if(a.state!=='COMPLETE'||!a.recommendation)return 'Resolve assessment issue';
 if(a.listing_changed)return 'Review changed job description';
 if(a.choice==='SKIP')return 'Revisit decision';
 if(a.choice)return 'Review application plan';
 return 'Review job';
}

export function processingStatus(r:Result):string|null {
 const a=r.assessment;
 if(a.state==='RUNNING')return 'Analyzing';
 if(a.state==='STALE'||a.listing_changed)return 'Outdated';
 if(a.state==='ERROR')return 'Analysis failed';
 if(a.state==='INTERRUPTED')return 'Interrupted';
 if(a.state==='PAUSED')return 'Paused';
 if(a.state!=='COMPLETE')return 'Not finished';
 if(!a.recommendation)return 'Recommendation unavailable';
 return null;
}

export function analysisFilter(r:Result,filter:string):boolean {
 const a=r.assessment;
 if(a.state!=='COMPLETE')return false;
 if(filter==='all')return true;
 if(filter==='unavailable')return !a.recommendation;
 if(filter==='review')return a.recommendation==='REVIEW';
 if(filter==='worth')return ['CASUAL','ATTACK_HARD'].includes(a.recommendation??'');
 return filter==='consider_passing'&&a.recommendation==='SKIP';
}

/** Most recent scan first, highest score within that scan; unscanned roles follow. */
export function recentResultsFirst(a:Result,b:Result):number {
 const stamp=(s?:string|null)=>{const n=s?Date.parse(s):0;return Number.isFinite(n)?n:0;};
 return (b.last_scan_id??0)-(a.last_scan_id??0)
  || (b.assessment.score??-1)-(a.assessment.score??-1)
  || stamp(b.assessment.assessed_at)-stamp(a.assessment.assessed_at)
  || a.company.localeCompare(b.company)
  || a.job_id.localeCompare(b.job_id);
}
