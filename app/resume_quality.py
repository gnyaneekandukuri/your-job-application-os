"""Approval gates for framework coverage and impact semantics."""
import math
import re
from .job_requirements import Strict
from pydantic import Field
from .retrieval import RetrievalError

class MetricCheck(Strict):
    claim: str
    supported: bool
    reason: str

class AuditedFinding(Strict):
    block_index: int
    supported: bool
    reason: str
    metrics: list[MetricCheck]

class FrameworkFinding(Strict):
    block_index: int
    meets_framework: bool
    context_quote: str
    action_quote: str
    result_quote: str
    measurement_quote: str
    reason: str

class QualityChecks(Strict):
    findings: list[AuditedFinding]
    framework_findings: list[FrameworkFinding]


def reconcile_audit(checks, expected, experience):
    """Retain unambiguous checks; missing/duplicate entries stay explicitly unchecked."""
    warnings = []
    def reconcile(rows, indices, fallback, label):
        grouped = {}
        for row in rows:
            grouped.setdefault(row.block_index, []).append(row)
        if set(grouped) - set(indices):
            warnings.append(f'{label}: review returned unrelated statement indices; those entries were ignored.')
        result = []
        for index in indices:
            entries = grouped.get(index, [])
            if len(entries) == 1:
                result.append(entries[0])
            else:
                reason = f'Not checked: the model returned {"duplicate checks" if entries else "no check"} for this statement. This is a review error, not proof the statement is false.'
                warnings.append(f'Statement {index+1}: {reason}')
                result.append(fallback(index, reason))
        return result
    checks.findings = reconcile(checks.findings, expected,
        lambda i,r: AuditedFinding(block_index=i,supported=False,reason=r,metrics=[]), 'Claim review')
    checks.framework_findings = reconcile(checks.framework_findings, experience,
        lambda i,r: FrameworkFinding(block_index=i,meets_framework=False,context_quote='',action_quote='',result_quote='',measurement_quote='',reason=r), 'Framework review')
    return warnings

EXPERIENCE={'experience','professional experience','work experience','employment','employment history','professional history','career history'}
NUMBER=re.compile(r'\d+(?:[.,]\d+)*(?:%|[kKmMbB]\b)?')

def experience_indices(blocks, master):
    def scoped(rows):
        explicit=any(b['kind']=='heading' and b['text'].strip().casefold() in EXPERIENCE for b in rows)
        inside=not explicit;found=set()
        for i,b in enumerate(rows):
            if b['kind']=='heading':inside=b['text'].strip().casefold() in EXPERIENCE
            if inside and b['kind']=='bullet':found.add(i)
        return found
    original_ids={master[i].get('item_id') for i in scoped(master)}
    return sorted(scoped(blocks)|{i for i,b in enumerate(blocks) if b.get('item_id') in original_ids and b.get('item_id')})

def metric_coverage(finding, text):
    intervals=[]
    for metric in finding.metrics:
        if not metric.claim or metric.claim not in text:
            raise RetrievalError('Impact checking returned a claim absent from the draft.',502)
        start=0
        while (start:=text.find(metric.claim,start))!=-1:
            intervals.append((start,start+len(metric.claim)));start+=len(metric.claim)
    if any(not any(a<=m.start() and m.end()<=b for a,b in intervals) for m in NUMBER.finditer(text)):
        raise RetrievalError('Impact checking omitted a numeric claim. The draft cannot be approved.',502)
    if any(not m.supported for m in finding.metrics):
        finding.supported=False
        finding.reason+=' '+ '; '.join(m.reason for m in finding.metrics if not m.supported)

def framework_summary(blocks,indices,findings,framework,supported):
    ids=[f.block_index for f in findings]
    if len(ids)!=len(set(ids)) or set(ids)!=set(indices):
        raise RetrievalError('Framework checking omitted or duplicated experience bullets.',502)
    failures=[];passed=0
    for f in findings:
        text=blocks[f.block_index]['text']
        parts=[f.action_quote,f.result_quote]
        if framework in ('PAR','STAR'):parts.append(f.context_quote)
        if framework=='XYZ':parts.append(f.measurement_quote)
        valid=all(q.strip() and q in text for q in parts)
        if f.meets_framework and valid and f.block_index in supported:passed+=1
        else:failures.append({'block_index':f.block_index,'reason':f.reason if not f.meets_framework else 'Required framework components or supporting evidence are missing.'})
    total=len(indices);required=math.ceil(total*0.8)
    return {'framework':framework,'passed':passed,'total':total,'required':required,
            'percent':round(100*passed/total,1) if total else None,'meets_threshold':passed>=required,
            'failures':failures,'status':'checked' if total else 'no_experience_bullets'}

def recurring_revenue_conflict(block, sources):
    """Flag ambiguous revenue types rather than treating equal amounts as equal metrics."""
    if not re.search(r'\bARR\b|annual recurring revenue',block.text,re.I):return ''
    def money(text):
        values=set()
        for m in re.finditer(r'\$\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmMbB])?',text):
            values.add(round(float(m[1].replace(',',''))*{'k':1000,'m':1000000,'b':1000000000}.get((m[2] or '').lower(),1),2))
        return values
    claimed=money(block.text)
    for source in sources:
        if source['id']=='resume' or source['id'].startswith('master:'):continue
        for sentence in re.split(r'(?<=[.!?])\s+|\n',source['text']):
            if money(sentence)&claimed and re.search(r'product revenue|one[- ]time revenue|customer commitments',sentence,re.I) and not re.search(r'\bARR\b|recurring',sentence,re.I):
                # A candidate clarification resolves only the named source and amount.
                resolved = set()
                for fact in sources:
                    resolution = fact.get('revenue_resolution') or {}
                    if (fact['id'].startswith('candidate_fact_rules.facts.') and fact.get('status') == 'USER_CONFIRMED'
                            and resolution.get('type') == 'ARR'
                            and source.get('path') in resolution.get('source_paths', [])):
                        resolved.add(resolution.get('amount_usd'))
                if (money(sentence) & claimed) <= resolved:
                    continue
                return 'Revenue type is unresolved: this amount is described as product revenue or customer commitments in '+source.get('title',source['id'])+'. It cannot be labeled ARR without explicit supporting evidence.'
    return ''
