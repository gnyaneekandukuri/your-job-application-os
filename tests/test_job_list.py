import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.job_list import JobList


def listing(n, **changes):
    return {'id':str(n),'company':'Startup','title':f'Product Manager {n}',
        'location':'USA','description':'Full responsibilities '*1000,
        'first_seen':'2026-10-01T12:00:00+00:00','last_seen':'2026-10-01T12:00:00+00:00',
        'freshness':'within_24h','sources':[{'board':'yc'},{'board':'linkedin'}],
        'assessment':{'state':'NOT_STARTED','score':None},
        'company_fit':{'group':'preferred','sort_order':0,'backing_boost':1,'evidence':[{'quote':'large evidence'}]},
        'location_match':{'status':'match'},'keyword_match':{'label':'High priority','priority':'high'},
        **changes}


@pytest.fixture
def model(tmp_path):
    root=tmp_path/'workspace';root.mkdir()
    source={'items':[listing(n) for n in range(61)],'company_preference':{'focus':'startups'},
        'assessment_results':{'items':[],'counts':{},'queued_count':0,'settings':{}},'boards':{'yc':'YC','linkedin':'LinkedIn'}}
    d=SimpleNamespace(path=tmp_path/'discovery.sqlite3',analysis=SimpleNamespace(library=SimpleNamespace(root=root)),
        worker=None,queued_ids=set(),table_view=Mock(side_effect=lambda **kw:copy.deepcopy(source)))
    result=JobList(d)
    return result,source,d


def test_saved_pages_do_not_recompute_and_details_are_not_transferred(model):
    m,source,d=model
    assert not m.page()['ready']
    assert m.refresh()
    first=m.page();second=m.page(page=2)
    assert first['total']==61 and first['pages']==3
    assert len(first['items'])==len(second['items'])==25
    assert not {i['id'] for i in first['items']} & {i['id'] for i in second['items']}
    assert all('description' not in i and i['description_length']>200 for i in first['items'])
    assert all('evidence' not in i['company_fit'] for i in first['items'])
    d.table_view.side_effect=AssertionError('Page load must not compute assessments')
    assert m.page(query='Manager 60')['total']==1  # Searches beyond first page.
    assert m.page(query="%' OR 1=1 --")['total']==0
    assert m.page(page=999)['page']==3
    assert m.page(board='yc')['total']==61
    assert m.page(board='a16z')['total']==0
    assert m.page(runs=True)['board_counts']=={'yc':61,'linkedin':61}
    assert m.assessments()['ready']
    assert not m.status()['refreshing']
    assert not m.refresh()
    assert d.table_view.call_count==1


def test_active_filter_keeps_selected_and_unknown_dates(model):
    m,source,d=model
    source['items']=[listing(1,age_state='active'),listing(2,age_state='older_than_four_days'),
                     listing(3,age_state='unknown'),listing(4,age_state='older_than_four_days',retention_protected=True)]
    m.refresh()
    assert {i['id'] for i in m.page(show='active')['items']}=={'1','3','4'}
    assert m.page(show='all')['total']==4
    assert m.page(show='kept')['total']==1


def test_filters_and_refresh_replace_prior_generation(model):
    m,source,d=model
    source['items']=[listing(1),listing(2,location_match={'status':'outside'}),
        listing(3,company_fit={'group':'established'}),
        listing(4,assessment={'state':'COMPLETE'}),
        listing(5,keyword_match={'label':'Not screened','priority':'worth_checking'},description='')]
    m.refresh()
    assert m.page()['total']==2
    assert m.page(location='all',company='all')['total']==4
    assert m.page(priority='high')['total']==1
    assert m.page(priority='not_screened',show='missing')['total']==1
    old=m.status()['revision']
    source['items']=[]
    (d.analysis.library.root/'preferences.json').write_text('{"roles":["Data PM"]}')
    assert m.status()['refreshing']
    assert m.page()['total']==2  # Last complete generation remains usable until replacement.
    m.refresh()
    assert m.page()['total']==0 and m.status()['revision']!=old
    assert not m.status()['refreshing']


def test_failure_retains_snapshot_and_exposes_error(model):
    m,source,d=model;m.refresh()
    old=m.status()['revision']
    (d.analysis.library.root/'changed.txt').write_text('changed')
    d.table_view.side_effect=RuntimeError('unavailable source')
    assert not m.refresh()
    assert m.status()['error'] and m.status()['refreshing']
    assert m.status()['revision']==old and m.page()['total']==61
    # Read model survives a server restart and has no authority to alter source data.
    reopened=JobList(d)
    assert reopened.page()['total']==61


def test_change_during_build_remains_marked_stale(model):
    m,source,d=model
    def changing(**kw):
        (d.analysis.library.root/'preferences.json').write_text('changed during build')
        return copy.deepcopy(source)
    d.table_view.side_effect=changing
    m.refresh()
    assert m.status()['refreshing']
    d.table_view.side_effect=lambda **kw:copy.deepcopy(source)
    m.refresh()
    assert not m.status()['refreshing']


def test_time_dependent_filters_expire_without_database_write(model,monkeypatch):
    m,_,_=model
    monkeypatch.setattr('app.job_list.time.time',lambda:6000)
    m.refresh()
    monkeypatch.setattr('app.job_list.time.time',lambda:6061)
    assert m.status()['refreshing']


def test_api_pagination_validation_and_lazy_detail(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup',headers={'X-Local-Review':'1'},json={'target_roles':['PM'],'countries':['US']})
        d=client.app.state.discovery
        oid,_=d.save('yc',{'source_id':'one','title':'PM','company':'Example','location':'USA',
            'url':'https://example.com/job','description':'Manage AI products. '*30,'posted_at':None})
        m=client.app.state.job_list
        assert m.refresh()
        response=client.get('/api/discovery/page?company=all').json()
        assert response['total']==1 and 'description' not in response['items'][0]
        assert client.get(f'/api/discovery/{oid}/detail').json()['description'].startswith('Manage AI')
        assert client.get('/api/discovery/missing/detail').status_code==404
        assert client.get('/api/discovery/page?size=100000').status_code==422
        assert client.get('/api/discovery/page?page=0').status_code==422
        assert client.get('/api/assessments/saved').json()['ready']


def test_failed_publish_rolls_back_deletes(model):
    from contextlib import closing
    m,source,d=model;m.refresh()
    old=m.status()['revision']
    with closing(m.connect()) as db, db:
        db.execute("CREATE TRIGGER fail_publish BEFORE INSERT ON listings BEGIN SELECT RAISE(ABORT,'test failure'); END")
    (d.analysis.library.root/'changed.txt').write_text('new version')
    assert not m.refresh()
    assert m.page()['total']==61 and m.status()['revision']==old
    assert m.status()['error']


def test_freshness_filters_keep_unknown_and_old_jobs_accessible(model):
    m, source, _ = model
    source['items'] = [listing(1), listing(2, freshness='older'),
                       listing(3, freshness='date_only'), listing(4, freshness='unknown'),
                       listing(5, freshness='within_24h', location_match={'status':'outside'})]
    m.refresh()
    assert m.page(show='recent')['total'] == 1
    assert m.page(show='older')['items'][0]['id'] == '2'
    assert m.page(show='uncertain')['total'] == 2
    assert m.page(show='all')['total'] == 4
    assert m.page(show='recent')['freshness_counts'] == {'recent':1, 'older':1, 'uncertain':2}
    assert m.page(show='recent',location='all')['freshness_counts']['recent'] == 2


def test_startup_focus_keeps_unknown_companies_visible(model):
    m,source,_=model
    source['items']=[listing(1),listing(2,company_fit={'group':'unknown','sort_order':100}),listing(3,company_fit={'group':'established'})]
    m.refresh()
    assert m.page()['total']==2
    assert m.page(company='startups')['total']==1


def test_confirmed_applications_leave_active_lists_without_rebuild(model):
    import sqlite3
    m,source,d=model
    source['items']=[listing(1,job_id='applied',url='https://jobs.example/1'),listing(2,job_id='new',url='https://jobs.example/2')]
    source['assessment_results']['items']=[{'job_id':'applied','url':'https://jobs.example/1','assessment':{'state':'COMPLETE'}},{'job_id':'new','url':'https://jobs.example/2','assessment':{'state':'COMPLETE'}}]
    m.refresh()
    with sqlite3.connect(m.path.parent/'applications.sqlite3') as db:
        db.execute('create table applications(job_id text,payload text)')
        db.execute('insert into applications values (?,?)',('applied',json.dumps({'url':'https://jobs.example/1'})))
    assert [r['id'] for r in m.page()['items']]==['2']
    assert m.page()['total']==1 and m.page()['freshness_counts']['recent']==1
    assert [r['job_id'] for r in m.assessments()['items']]==['new']
    assert m.assessments()['counts']['complete']==1
    with sqlite3.connect(m.path.parent/'applications.sqlite3') as db:
        assert db.execute('select count(*) from applications').fetchone()[0]==1


def test_unanalyzed_application_hidden_by_opportunity_id(model):
    import sqlite3
    m,source,_=model
    source['items']=[listing(1,job_id=None,url=''),listing(2,job_id=None,url='')]
    m.refresh()
    with sqlite3.connect(m.path.parent/'applications.sqlite3') as db:
        db.execute('create table applications(job_id text,payload text)')
        db.execute('insert into applications values (?,?)',('opportunity:1',json.dumps({'url':''})))
    assert [r['id'] for r in m.page()['items']]==['2']
    assert m.page()['total']==1


def test_exact_duplicate_grouping_and_applied_alias_without_rebuild(model):
    import sqlite3
    from app.job_identity import identity
    m,source,d=model
    one=listing(1,url='https://example.com/1')
    two={**one,'id':'2','url':'https://example.com/2'}
    different={**one,'id':'3','url':'https://example.com/3','description':one['description']+' Different responsibilities.'}
    source['items']=[one,two,different]
    assert m.refresh()
    page=m.page()
    assert page['total']==2
    grouped=next(r for r in page['items'] if len(r['duplicate_ids'])==2)
    assert set(grouped['listing_urls'])=={one['url'],two['url']}
    with sqlite3.connect(d.path) as db:
        db.execute('CREATE TABLE opportunities(id TEXT,job_id TEXT,payload TEXT)')
        db.executemany('INSERT INTO opportunities VALUES(?,NULL,?)',[(r['id'],json.dumps(r)) for r in source['items']])
    with sqlite3.connect(d.path.parent/'applications.sqlite3') as db:
        db.execute('CREATE TABLE applications(job_id TEXT,payload TEXT)')
        db.execute('INSERT INTO applications VALUES(?,?)',('opportunity:1',json.dumps({'url':one['url'],'job_snapshot':one,'location':one['location']})))
    assert [r['id'] for r in m.page()['items']]==['3']
    assert identity({**one,'location':'Other city'})!=identity(one)
    assert identity({**one,'description':''}) is None

def test_manual_queue_visible_before_snapshot_exists(model):
    m,source,d=model
    item={'job_id':'manual','company':'Disney','title':'PM','url':'https://example.org','assessment':{'state':'QUEUED','score':None,'recommendation':None}}
    d.analysis.manual_queue=SimpleNamespace(display_items=lambda:[item])
    assert m.assessments()['items']==[item]
    assert m.assessments()['ready']
    source['assessment_results']['items']=[{**item,'assessment':{'state':'NOT_STARTED'}}]
    m.refresh()
    assert m.assessments()['items']==[item]
