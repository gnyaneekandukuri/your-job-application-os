/** Review guidance from saved assessments only. Never changes scores or facts. */
export type GapRequirement = {id:string;interpretation:string;quote:string;category:string};
export type GapMatch = {judgment:{support:string;limitation:string;clarification:string;assessment?:{alignment:string;confidence:string;review_required?:boolean;user_confirmed?:boolean}|null};passages:{source_title:string;quote:string;source_path:string;start_line:number}[]};
export type PursuitEvidence = {constraints:{kind:string;finding:string;quote:string;reason:string}[];core_evidence:{requirement_id:string}[]};
export type Gap = {id:string;requirementId:string;kind:'blocker'|'experience'|'documentation'|'clarification';label:string;title:string;reason:string;question:string;impact:string;jdQuote:string;evidence:GapMatch['passages'];priority:number};

const constraintNames:Record<string,string>={location:'Location',h1b_transfer:'H-1B transfer',role_content:'Role content',core_coding:'Coding expectations'};
const constraintQuestions:Record<string,string>={
  location:'Does this location or work arrangement conflict with your current preferences, or should those preferences be reviewed?',
  h1b_transfer:'Do you have explicit employer information about H-1B transfers that changes this interpretation?',
  role_content:'Does the main work described here match the roles you want to pursue?',
  core_coding:'Does the expected hands-on coding match the work you want to do?'
};

export function buildGapReview(requirements:GapRequirement[],results:Record<string,GapMatch>,pursuit:PursuitEvidence|null):Gap[]{
  const core=new Set(pursuit?.core_evidence.map(e=>e.requirement_id)??[]);
  const gaps:Gap[]=[];
  for(const signal of pursuit?.constraints??[]){
    if(signal.finding==='compatible')continue;
    // A conflict without a source quote is uncertainty, never a blocker.
    const blocker=signal.finding==='incompatible'&&!!signal.quote.trim();
    gaps.push({id:`constraint:${signal.kind}`,requirementId:'',kind:blocker?'blocker':'clarification',
      label:blocker?'Explicit constraint conflict':'Needs confirmation',title:constraintNames[signal.kind]??signal.kind,
      reason:signal.reason,question:constraintQuestions[signal.kind]??'What should we clarify about this condition?',
      impact:blocker?'The saved assessment treats this conflict as a reason to skip. Review the JD interpretation and your preferences.':'Unresolved does not mean disqualified. This is a preference or eligibility check, not evidence of missing ability.',
      jdQuote:signal.quote,evidence:[],priority:blocker?0:4});
  }
  for(const req of requirements){
    const match=results[req.id];
    if(!match)continue;
    const a=match.judgment.assessment;
    if(a?.review_required&&!a.user_confirmed){
      gaps.push({id:req.id,requirementId:req.id,kind:'clarification',label:'Tentative classification',title:req.interpretation,
        reason:match.judgment.limitation||'The classifier is uncertain about part of this assessment.',
        question:'Do these dimension judgments reflect the quoted experience, or should we recheck the evidence?',
        impact:'The score is provisional. Model uncertainty does not establish a missing skill.',jdQuote:req.quote,evidence:match.passages,priority:1});
      continue;
    }
    // Strong transferable evidence remains strong: a different domain is not a gap.
    if(a?.alignment==='strong'||(!a&&match.judgment.support==='direct'))continue;
    const ambiguous=req.category==='unclear';
    const partial=!ambiguous&&a?.alignment==='partial'&&a.confidence!=='low'&&match.passages.length>0;
    const kind=ambiguous?'clarification':partial?'experience':'documentation';
    const importance=core.has(req.id)?'Core work':req.category==='requirement'?'Explicit requirement':req.category==='preference'?'Preferred qualification':'Responsibility';
    gaps.push({id:req.id,requirementId:req.id,kind,label:ambiguous?'Requirement unclear':partial?'Experience coverage gap':'Missing documentation',
      title:req.interpretation,reason:match.judgment.limitation||'The saved evidence does not establish the full requirement.',
      question:match.judgment.clarification.trim()||`Which employer or project provides an example of ${req.interpretation}, and what did you personally own?`,
      impact:ambiguous?'Clarify the JD wording first; ambiguous requirements are excluded from scoring.':
        `${importance}. ${partial?'The cited experience covers part of the work; this does not prove you lack the rest.':'The sources do not establish this detail; this does not prove a missing skill.'} ${req.category==='preference'?'This is not a mandatory qualification.':'Additional relevant evidence may change the assessment; it is not automatically a blocker.'}`,
      jdQuote:req.quote,evidence:match.passages,priority:ambiguous?5:core.has(req.id)?1:req.category==='requirement'?2:req.category==='responsibility'?3:6});
  }
  return gaps.sort((a,b)=>a.priority-b.priority||a.id.localeCompare(b.id));
}
