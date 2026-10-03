"""Source-backed company preference routing, separate from capability scoring."""
import json
import re
from functools import lru_cache
from pathlib import Path
from .extraction import plain_text

CATALOG = json.loads((Path(__file__).resolve().parents[1]/'criteria/company-evidence.json').read_text(encoding='utf-8'))
STAGE = re.compile(r'\bseries[ -]+([a-h])\b', re.I)


def identity(name):
    return re.sub(r'[^a-z0-9]', '', re.sub(r'\b(inc|llc|ltd|co|corp|corporation)\b', '', name.lower()))


def preferences(root, discovery):
    profile=json.loads((root/'knowledge/profile.json').read_text(encoding='utf-8'))
    company=profile.get('pursuit_preferences',{}).get('company',{})
    focus=company.get('focus') or ('startups' if company.get('startup_preference') else 'any')
    return {'focus':focus,'startup_preference':focus=='startups',
            'preferred_stages':discovery.get('preferred_funding_terms',[]),
            'strong_backing_preferred':company.get('strong_investor_backing_preferred',False)}


def company_fit(item, preference):
    # Reuse unchanged public listing evidence across refreshes; any preference or
    # relevant listing edit changes the key. No provider calls are made here.
    return dict(_company_fit(item.get('company',''), item.get('description',''),
                             item.get('funding') or '', item.get('url'),
                             preference.get('focus', 'startups' if preference['startup_preference'] else 'any'),
                             tuple(preference['preferred_stages']), preference.get('strong_backing_preferred',False)))


@lru_cache(maxsize=512)
def _company_fit(name, description, funding, url, focus, wanted_stages, prefer_backing):
    item={'company':name,'description':description,'funding':funding,'url':url}
    preference={'focus':focus,'startup_preference':focus=='startups','preferred_stages':wanted_stages,'strong_backing_preferred':prefer_backing}
    evidence=[];stages=set();startup=False;established=False;backed=False
    name=item.get('company','');key=identity(name)
    for record in CATALOG['companies']:
        if key in [identity(v) for v in record['names']]:
            established=record['type']=='established'
            evidence.append({'field':'company_reference','quote':record['fact'],'url':record['url'],'checked_at':record['checked_at']})
    funding=(item.get('funding') or '').strip()
    if funding.lower() not in ('','unknown','not provided'):
        stages.update('series '+m.lower() for m in STAGE.findall(funding))
        if re.fullmatch(r'(?:pre[ -]seed|seed|growth|early stage|late stage)',funding,re.I):stages.add(funding.lower())
        if stages:evidence.append({'field':'funding','quote':funding,'url':item.get('url')})
        if re.fullmatch(r'(?:public|ipo|post[ -]ipo)',funding,re.I):
            established=True;evidence.append({'field':'funding','quote':funding,'url':item.get('url')})
    # Only employer assertions count, never candidate requirements/customer descriptions.
    text=plain_text(item.get('description',''))
    subject=r'(?:we|our company|'+re.escape(name)+r')'
    for sentence in re.split(r'(?<=[.!?])\s+|\n+',text):
        if not sentence.strip():continue
        if re.search(r'\b(?:experience|worked|working|customers?|clients?|portfolio|invest in|seeking|planning|plan to|aim to|hope to|not|never)\b',sentence,re.I):continue
        assertion=re.search(r'\b'+subject+r"(?:\s+(?:is|are|have|has|recently|just|successfully|now)|['’]re|['’]ve)\b",sentence,re.I)
        if not assertion:continue
        claim=sentence[assertion.start():]
        found=set('series '+m.lower() for m in STAGE.findall(claim)) if re.search(r'\b(raised|closed|secured|funded|funding|round|company|startup)\b',claim,re.I) else set()
        is_startup=bool(re.search(r'\b(?:start[ -]?up|seed[ -]stage|early[ -]stage)\b',claim,re.I))
        public=bool(re.search(r'\b(?:publicly[ -]traded|publicly[ -]listed|fortune\s+(?:100|500)|listed on (?:the )?(?:nyse|nasdaq))\b',claim,re.I))
        backing=bool(re.search(r'\b(?:backed|funded) by\b',claim,re.I))
        if found or is_startup or public or backing:
            stages.update(found);startup|=is_startup;established|=public;backed|=backing
            evidence.append({'field':'description','quote':sentence.strip(),'url':item.get('url')})
    # The highest reported round prevents a historical Series A mention from winning.
    series=sorted(s for s in stages if s.startswith('series '))
    stage=series[-1] if series else next(iter(sorted(stages)),None)
    startup=startup or bool(stage)
    wanted=[s.lower() for s in preference['preferred_stages']]
    if established:group='established';label='Established company';order=90
    elif startup:
        matched=not wanted or stage in wanted
        group='preferred' if matched else 'other_startup'
        label=(stage.title()+' reported') if stage else 'Startup · stage unknown'
        order=(wanted.index(stage) if stage in wanted else 0 if not wanted else len(wanted)+1)
    else:group='unknown';label='Company stage unknown';order=100
    focus=preference.get('focus', 'startups' if preference['startup_preference'] else 'any')
    allowed=(focus=='any' or (focus=='established' and group=='established') or (focus=='startups' and group in ('preferred','other_startup')))
    return {'group':group,'label':label,'stage':stage,'backing_reported':backed,'evidence':evidence,
            'automatic_allowed':allowed,'sort_order':order, 'backing_boost':int(backed and preference.get('strong_backing_preferred',False)),
            'reason':('Matches your company preferences.' if allowed else
                      'Company type does not match your established-company preference.' if focus=='established' else
                      'Established company, outside your startup preference.' if established else
                      'Startup reported; funding stage is a preference, not an exclusion.' if startup else
                      'No employer-specific startup or funding evidence is available.')}


def apply_company_priority(items):
    for item in items:
        match=item.get('keyword_match');fit=item.get('company_fit')
        if match and fit and not fit['automatic_allowed'] and match['priority']=='high':
            match.update(priority='worth_checking',label='Worth checking',reason=fit['reason']+' Role alignment remains available for manual assessment.')
        elif match and fit and not fit['automatic_allowed'] and match.get('label')=='Not screened':
            match['reason']=fit['reason']+' You can review its role match and select it for manual assessment.'
