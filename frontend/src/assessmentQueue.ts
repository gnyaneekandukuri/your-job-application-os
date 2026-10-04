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

export function analysisFilter(r:Result,filter:string):boolean {
 const a=r.assessment;
 const completed=a.state==='COMPLETE'&&!!a.recommendation;
 if(filter==='issues')return !completed||!!a.listing_changed||a.recommendation==='REVIEW';
 if(filter==='all')return a.state==='COMPLETE';
 if(filter==='passed')return completed&&a.choice==='SKIP';
 if(!completed||a.listing_changed||a.choice==='SKIP')return false;
 return filter==='worth'?['CASUAL','ATTACK_HARD'].includes(a.recommendation!):filter==='consider_passing'?a.recommendation==='SKIP':false;
}
