"""Explain the latest scan and current follow-up without running assessment."""
from collections import Counter


def blocker(item):
    assessment = item.get('assessment') or {}
    if item.get('description_changed'):
        return 'The job description changed. Review it before reassessing.', 'jd'
    if assessment.get('state') == 'STALE':
        return 'The previous assessment is outdated. Review or reassess it.', 'assessment'
    if assessment.get('state') in ('ERROR','INTERRUPTED','NEEDS_ATTENTION'):
        return assessment.get('error') or assessment.get('stage') or 'Analysis needs review.', 'assessment'
    if assessment.get('state') == 'COMPLETE':
        return 'An assessment already exists. Review its recommendation.', 'assessment'
    if item.get('location_match',{}).get('status') != 'match':
        return 'The location does not confirm your preferred country.', 'listing'
    if not item.get('company_fit',{}).get('automatic_allowed',True):
        return item.get('company_fit',{}).get('reason','Company preference needs review.'), 'listing'
    if len(item.get('description','')) < 200:
        return 'The full job description is needed.', 'jd'
    if item.get('automatic_assessment'):
        return 'A previous automatic attempt exists. Review before retrying.', 'assessment'
    if item.get('keyword_match',{}).get('label') == 'Not screened':
        return 'Initial matching needs refreshing after an input change.', 'listing'
    if item.get('automatic_candidate'):
        return 'Queued for automatic deep analysis', 'listing'
    return 'Ready for assessment; no automatic assessment was started in this scan.', 'listing'


def overview(result, items):
    reports = result.get('run_reports') or []
    if not reports: return None
    scan = reports[0]
    roles = scan.get('roles',[])
    counts = Counter(r.get('priority','Not screened') for r in roles)
    current = {i['id']:i for i in items}
    attention = []
    for role in roles:
        if role.get('priority') != 'High priority': continue
        item = current.get(role['id'])
        if not item: continue
        state = (item.get('assessment') or {}).get('state')
        if state == 'RUNNING' or item.get('queued_for_assessment'): continue
        reason, action = blocker(item)
        attention.append({'id':item['id'],'company':item['company'],'title':item['title'],
                          'job_id':item.get('job_id'),'reason':reason,'action':action})
    analyses = result.get('assessment_results',{}).get('items',[])
    completed = [i for i in analyses if i['assessment']['state']=='COMPLETE' and i['assessment'].get('recommendation')]
    worth = sum(i['assessment'].get('recommendation') in ('CASUAL','ATTACK_HARD') and
                i['assessment'].get('choice') != 'SKIP' and not i['assessment'].get('listing_changed') for i in completed)
    sources = scan.get('sources',{})
    failures = [{'board':board,'state':v['state'],'reason':v.get('diagnostic',{}).get('reason') or v.get('error','Source needs attention.')}
                for board,v in sources.items() if v.get('state') not in ('checked','partial')]
    return {'started':scan['started'],'finished':scan.get('finished'),'state':scan['state'],
            'tracked':scan.get('tracked',False),'seen':len(roles),'new':scan.get('new_count'),
            'high':counts['High priority'],'worth_checking':counts['Worth checking'],
            'requested':sum(bool(r.get('assessment_requested')) for r in roles),
            'completed':len(completed),'worth_applying':worth,'attention':attention,
            'failures':failures,'error':scan.get('error'),
            'auto_paused':not result.get('settings',{}).get('auto_assess',False)}
