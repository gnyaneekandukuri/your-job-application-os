export type Explanation={summary:string;strengths:{requirement_id:string;requirement:string;alignment:string;tentative:boolean;citations:{source_title:string;source_path:string;start_line:number;quote:string}[]}[];gaps:{requirement_id:string;requirement:string;detail:string;tentative:boolean}[];decision_basis:{label:string;passed:boolean;detail:string}[]};
export type Assessment = {assessed_at?:string|null;explanation?:Explanation;state:string;score:number|null;provisional?:boolean;listing_changed?:boolean;recommendation:string|null;rationale?:string;choice?:string|null;stage?:string;error?:string;recommendation_error?:string};
export type Result = {yc_listing?:boolean;last_scan_id?:number|null;last_scanned_at?:string|null;job_id:string;opportunity_id?:string|null;company:string;title:string;url:string;assessment:Assessment};
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
 if(filter==='passed')return a.choice==='SKIP';
 // Outdated results keep their score; they stay visible under All with an Outdated tag until re-run.
 if(filter==='all'&&a.choice!=='SKIP'&&a.state==='STALE'&&a.score!==null)return true;
 if(a.choice==='SKIP'||a.state!=='COMPLETE')return false;
 if(filter==='all')return true;
 if(filter==='unavailable')return !a.recommendation;
 if(filter==='review')return a.recommendation==='REVIEW';
 if(filter==='worth')return ['CASUAL','ATTACK_HARD'].includes(a.recommendation??'');
 return filter==='consider_passing'&&a.recommendation==='SKIP';
}

/** Most recent scan first, highest score within that scan; unscanned roles follow. */
export function recentResultsFirst(a:Result,b:Result):number {
 const stamp=(s?:string|null)=>{const n=s?Date.parse(s):0;return Number.isFinite(n)?n:0;};
 // Most recently analyzed first, so a job just analyzed from New jobs is at the top.
 return stamp(b.assessment.assessed_at)-stamp(a.assessment.assessed_at)
  || (b.assessment.score??-1)-(a.assessment.score??-1)
  || (b.last_scan_id??0)-(a.last_scan_id??0)
  || a.company.localeCompare(b.company)
  || a.job_id.localeCompare(b.job_id);
}

/** Plain-language request for an analyzed job that cannot show a usable result. */
export type HelpAction='rerun'|'fix_description'|'open';
export function helpFor(r:Result):{issue:string;todo:string;action:HelpAction}|null {
 const a=r.assessment;if(a.choice==='SKIP'||!processingStatus(r)||['RUNNING','QUEUED'].includes(a.state))return null;
 if(a.state==='STALE'&&a.score!==null&&!a.listing_changed)return null; // shown in Results with a Re-run action
 const error=a.recommendation_error||a.error||'';
 const fix:HelpAction=r.opportunity_id?'fix_description':'open';
 if(a.listing_changed)return {issue:'The job listing changed after you analyzed it.',todo:'Review the updated description, then analyze it again.',action:fix};
 if(/too long to classify/i.test(error))return {issue:"The saved description includes website text that isn't part of the job (such as a country list), so it can't be analyzed.",todo:'Open the listing, copy just the job description, and paste it.',action:fix};
 if(/too many jd criteria/i.test(error))return {issue:'The description lists too many requirements to score reliably.',todo:'Check the description for repeated or non-job text, fix it, then analyze again.',action:fix};
 if(/budget|limit|quota|rate/i.test(error))return {issue:'The API budget or provider limit was reached during analysis.',todo:'Check API costs, then retry.',action:'rerun'};
 if(a.state==='STALE')return {issue:'Your experience or settings changed after this analysis, so its score may be out of date.',todo:'Re-run to update the score.',action:'rerun'};
 if(['ERROR','INTERRUPTED','PAUSED','NEEDS_ATTENTION'].includes(a.state))return {issue:'The analysis stopped before finishing'+(error?`: ${error}`:'.'),todo:'Retry. Steps that already finished are reused.',action:'rerun'};
 if(a.state==='COMPLETE')return {issue:"The score finished, but the apply/pass recommendation didn't.",todo:'Open the assessment and retry the recommendation.',action:'open'};
 return {issue:"The analysis hasn't finished.",todo:'Open it to continue.',action:'open'};
}
