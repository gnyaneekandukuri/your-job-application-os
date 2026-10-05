export type Assessment = {state:string;score:number|null;provisional?:boolean;listing_changed?:boolean;recommendation:string|null;rationale?:string;choice?:string|null;stage?:string;error?:string;recommendation_error?:string};
export type Result = {job_id:string;opportunity_id?:string|null;company:string;title:string;url:string;assessment:Assessment};
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
