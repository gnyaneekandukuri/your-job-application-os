from fastapi.testclient import TestClient
from app.main import create_app
from app.company_preferences import company_fit, apply_company_priority
from app.discovery import Discovery


POLICY = {'focus':'startups','startup_preference':True,'preferred_stages':['series a','series b'],'strong_backing_preferred':True}


def fit(description='', **fields):
    return company_fit({'company':'Example Labs','description':description, **fields}, POLICY)


def test_stage_order_and_historical_rounds():
    a=fit('We have raised Series A funding.')
    b=fit('We raised Series A funding in 2020. We have closed our Series B round.')
    assert a['automatic_allowed'] and b['automatic_allowed']
    assert a['sort_order'] < b['sort_order']
    assert b['stage']=='series b'
    assert fit(funding='Series C')['automatic_allowed']
    assert fit(funding='Series C')['sort_order'] > b['sort_order']
    assert fit('We are an AI startup.')['automatic_allowed']


def test_no_stage_inference_from_board_or_candidate_requirements():
    for text in ['Experience at a Series A startup required.', 'We are seeking experience with Series A startup customers.', 'We are planning a Series A funding round.']:
        result=fit(text, sources=[{'board':'yc'}])
        assert result['group']=='unknown' and not result['automatic_allowed']
    assert fit('We are an AI startup.')['group']=='other_startup'


def test_established_facts_override_old_funding_and_do_not_change_capability():
    result=fit('We have raised Series A funding.',company='Capital One')
    assert result['group']=='established' and not result['automatic_allowed']
    item={'company_fit':result,'keyword_match':{'priority':'high'},'assessment':{'score':88}}
    apply_company_priority([item])
    assert item['keyword_match']['priority']=='worth_checking'
    assert item['assessment']['score']==88
    assert not Discovery.auto_eligible({**item,'keyword_match':{'priority':'high'},'location_match':{'status':'match'},'description':'full JD '*100})


def test_candidate_focus_changes_routing():
    item={'company':'Capital One','description':''}
    assert not company_fit(item,POLICY)['automatic_allowed']
    for focus in ('any','established'):
        assert company_fit(item,{**POLICY,'focus':focus})['automatic_allowed']


def test_preferences_persist_per_candidate_and_validate(tmp_path):
    headers={'X-Local-Review':'1'}
    with TestClient(create_app(tmp_path/'one')) as first, TestClient(create_app(tmp_path/'two')) as second:
        payload={'target_roles':['AI Product Manager'],'countries':['US'],'company_focus':'startups','preferred_funding_stages':['Series A','Series B'],'strong_investor_backing_preferred':True}
        assert first.post('/api/setup',headers=headers,json=payload).status_code==200
        first.post('/api/setup',headers=headers,json={'target_roles':['Data PM'],'countries':['US']})
        saved=first.get('/api/setup').json()
        assert saved['company_focus']=='startups'
        assert saved['preferred_funding_stages']==['series a','series b']
        assert second.get('/api/setup').json()['company_focus']=='any'
        assert first.post('/api/setup',headers=headers,json={**payload,'preferred_funding_stages':['made up']}).status_code==422
        overview=first.get('/api/discovery').json()['pipeline']
        assert overview==dict(complete=0,running=0,attention=0,previous=0,queued=0)
