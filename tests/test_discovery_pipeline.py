from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.discovery import Discovery, Settings
from app.extraction import ashby_reference, ashby_target
from app.safe_fetch import ExtractionError


def row(description=''):
    return {'source_id':'example','title':'Product Manager','company':'Example Labs',
            'url':'https://example.com/jobs/pm','location':'New York, NY, USA',
            'description':description,'posted_at':None}


def configure(client):
    client.post('/api/setup', headers={'X-Local-Review':'1'}, json={'target_roles':['Product Manager'],'countries':['US']})
    return client.app.state.discovery


def test_metadata_sighting_cannot_mix_date_and_provenance(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        discovery = configure(client)
        discovery.save('cosign', {**row(), 'posted_at':'2026-10-04T12:00:00+00:00',
                                 'date_basis':'board_timestamp', 'date_label':'Cosign posting timestamp'})
        discovery.save('lenny', {**row(), 'date_basis':'unknown', 'date_label':'Updated today'})
        item = discovery.view(include_analysis=False)['items'][0]
        assert item['date_basis'] == 'board_timestamp'
        assert item['date_label'] == 'Cosign posting timestamp'
        assert item['posted_at'] == '2026-10-04T12:00:00+00:00'


@pytest.mark.parametrize('date',['older','unknown','date_only','within_24h'])
def test_priority_not_posting_age_controls_eligibility(date):
    item={'keyword_match':{'priority':'high'},'location_match':{'status':'match'},
          'freshness':date,'description':'A complete description of the job responsibilities. '*5}
    assert Discovery.auto_eligible(item)
    assert not Discovery.auto_eligible({**item,'automatic_assessment':{'state':'FAILED'}})
    assert Discovery.auto_eligible({**item,'description_changed':True,'assessment':{'state':'JD_CHANGED'}})
    assert not Discovery.auto_eligible({**item,'location_match':{'status':'outside'}})
    assert not Discovery.auto_eligible({**item,'description':''})
    for state in ('COMPLETE', 'RUNNING', 'ERROR', 'INTERRUPTED'):
        assert not Discovery.auto_eligible({**item,'assessment':{'state':state}})
    assert Discovery.auto_eligible({**item,'assessment':{'state':'NOT_STARTED'}})


def test_fetch_match_is_saved_but_wrong_company_needs_paste(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        oid,_=discovery.save('yc',row())
        draft={'title':'Product Manager','company':'Wrong Employer','description':'Responsibilities: research customers and deliver products. '*20}
        monkeypatch.setattr('app.description_worker.extract_bounded',lambda _:draft)
        assert discovery.complete_descriptions({oid})['needs_paste']==1
        assert discovery.view()['items'][0]['description']==''
        # Retry is bounded: a failed automatic fetch is not repeated each poll/scan.
        monkeypatch.setattr('app.description_worker.extract_bounded',lambda _:pytest.fail('Unexpected repeat fetch'))
        discovery.complete_descriptions({oid})
        with discovery.db() as db: db.execute('DELETE FROM description_fetches')
        draft['company']='Example Labs'
        monkeypatch.setattr('app.description_worker.extract_bounded',lambda _:draft)
        assert discovery.complete_descriptions({oid})['fetched']==1
        saved=discovery.view()['items'][0]
        assert saved['description']==draft['description']
        assert saved['description_provenance']['method']=='listing_extraction'
        assert saved['job_id'] is None  # Fetching alone never spends on analysis.


def test_pipeline_completes_descriptions_before_assessment(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        discovery.configure(Settings(enabled=False,auto_assess=True))
        monkeypatch.setattr('app.discovery.BOARDS',{'yc':'YC'})
        discovery.collector=lambda _:([row()],'Category only; partial coverage',[])
        events=[]
        discovery.complete_descriptions=lambda ids:events.append(('descriptions',ids)) or {'fetched':0,'needs_paste':1}
        discovery.assess_high_priority=lambda ids:pytest.fail('Scan waited for deep analysis')
        discovery.scan()
        assert [v[0] for v in events]==['descriptions']
        assert discovery.view()['scan']['payload']['state']=='complete'
        assert discovery.view()['scan']['payload']['sources']['yc']['coverage_status']=='partial'
        assert discovery.view()['scan']['finished']


def test_encoded_ashby_company_segment_stays_on_fixed_api_host():
    job=str(uuid4())
    ref=ashby_reference('https://jobs.ashbyhq.com/Example%20Labs/'+job)
    assert ashby_target(ref)=='https://api.ashbyhq.com/posting-api/job-board/Example%20Labs'
    with pytest.raises(ExtractionError):
        ashby_reference('https://jobs.ashbyhq.com/Example%2fOther/'+job)


def test_automatic_dispatch_is_not_repeated_after_unknown_paid_failure(tmp_path, monkeypatch):
    from app.retrieval import RetrievalError
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        discovery.configure(Settings(enabled=False,auto_assess=True))
        oid,_=discovery.save('yc',row('A full saved description of product responsibilities.'))
        monkeypatch.setattr(discovery.analysis,'view',lambda job:{'run':None,'ready':True,'input_version':'a'*64})
        attempts=[]
        def fail(job, data):
            attempts.append(job.id)
            raise RetrievalError('Provider outcome unknown')
        monkeypatch.setattr(discovery.analysis,'start',fail)
        with pytest.raises(RetrievalError):
            discovery.import_role(oid,automatic=True)
        discovery.import_role(oid,automatic=True)
        assert len(attempts)==1
        with discovery.db() as db:
            assert db.execute('SELECT COUNT(*) FROM automatic_assessments').fetchone()[0]==1


def test_polling_does_not_reconcile_review_graph(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        oid,_=discovery.save('yc',row('A full saved description of product responsibilities.'))
        discovery.import_role(oid,analyze=False)
        monkeypatch.setattr(discovery.service,'review',lambda _:pytest.fail('Polling reconciled review graph'))
        assert discovery.view()['items'][0]['assessment']['state']=='NOT_STARTED'


def test_waiting_jobs_are_explicitly_queued(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        discovery.configure(Settings(enabled=False,auto_assess=True))
        base={'keyword_match':{'priority':'high'},'location_match':{'status':'match'},'description':'A full saved description of product responsibilities. '*5}
        items=[{**base,'id':'one'},{**base,'id':'two'}]
        monkeypatch.setattr(discovery,'view',lambda **kw:{'items':[i for i in items if kw.get('opportunity_ids') is None or i['id'] in kw['opportunity_ids']]})
        queued=[]
        monkeypatch.setattr(discovery,'import_role',lambda *a,**k:queued.append(set(discovery.queued_ids)))
        discovery.assess_high_priority({'one','two'})
        assert queued==[{'two'},set()]
        assert not discovery.queued_ids


def test_provider_limit_pauses_queue_but_not_collection(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        discovery.configure(Settings(enabled=True,auto_assess=True))
        base={'keyword_match':{'priority':'high'},'location_match':{'status':'match'},'description':'A full saved description of product responsibilities. '*5}
        monkeypatch.setattr(discovery,'view',lambda **kw:{'items':[{**base,'id':i} for i in ('one','two') if kw.get('opportunity_ids') is None or i in kw['opportunity_ids']]})
        attempts=[]
        def imported(oid,**kwargs):
            attempts.append(oid)
            return {'job_id':'job-one'}
        monkeypatch.setattr(discovery,'import_role',imported)
        monkeypatch.setattr(discovery,'assessment_summary',lambda item:{'error':'Limit reached (HTTP 429)'})
        discovery.assess_high_priority({'one','two'})
        assert attempts==['one']
        assert discovery.settings()['enabled'] is True
        assert discovery.settings()['auto_assess'] is False
        assert '429' in discovery.settings()['auto_assess_pause_reason']


def test_results_include_manual_roles_and_separate_unstarted(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery=configure(client)
        for title in ('Completed role','Failed role','Unstarted role'):
            response=client.post('/api/jobs',headers={'X-Local-Review':'1'},json={'request_id':str(uuid4()),'company':'Example Labs','title':title,'description':'Own customer research and delivery of reporting products.'})
            assert response.status_code==201
        def summary(item):
            title=discovery.service.store.read(item['job_id'])[0].title
            state={'Completed role':'COMPLETE','Failed role':'ERROR','Unstarted role':'NOT_STARTED'}[title]
            return {'state':state,'score':80 if state=='COMPLETE' else None,'recommendation':'CASUAL' if state=='COMPLETE' else None}
        monkeypatch.setattr(discovery,'assessment_summary',summary)
        result=client.get('/api/assessments')
        assert result.status_code==200
        assert {r['title'] for r in result.json()['items']}=={'Completed role','Failed role'}


def test_empty_automatic_dispatch_does_not_recheck_every_listing(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client)
        calls=[]
        def view(**kw):
            calls.append(1)
            return {'items':[{'id':str(i),'keyword_match':{'priority':'worth_checking'}} for i in range(100)]}
        monkeypatch.setattr(d,'view',view)
        d.assess_high_priority({str(i) for i in range(100)})
        assert len(calls)==1


@pytest.mark.parametrize('location,country,status',[
    ('New York City','US','match'),('NYC Office','US','match'),('San Francisco','US','match'),
    ('San Francisco, CA','US','match'),('San Francisco, Philippines','US','outside'),
    ('Remote except New York City','US','unclear'),('San Francisco','ZA','outside'),
    ('Remote','US','unclear'),('Cambridge','US','unclear')])
def test_city_labels_do_not_override_foreign_country_or_remote_scope(location,country,status):
    from app.discovery_location import location_match
    assert location_match(location,{'location_filter':{'countries':[country]}})['status']==status


@pytest.mark.parametrize('priority',['low','high','worth_checking',None])
def test_manual_selection_is_allowed_at_every_initial_priority(tmp_path,monkeypatch,priority):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client)
        oid,_=d.save('yc',row('Own product strategy and delivery. '*10))
        item=d.view()['items'][0]
        item['keyword_match']={'priority':priority}
        monkeypatch.setattr(d,'view',lambda:{'items':[item]})
        monkeypatch.setattr(d.selected,'start',lambda:None)
        response=client.post('/api/discovery/assess-selected',headers={'X-Local-Review':'1'},json={'roles':[{'id':oid,'content_hash':item['content_hash']}]})
        assert response.status_code==200
        assert {k:response.json()[k] for k in ('accepted','rejected')}=={'accepted':[oid],'rejected':[]}


@pytest.mark.parametrize('location,expected',[('match',True),('unclear',True),('outside',False)])
def test_initial_screening_location_precedes_company_preferences(location,expected):
    for group in ('preferred','other_startup','established','unknown'):
        assert Discovery.initial_screening_eligible({'location_match':{'status':location},
            'company_fit':{'group':group,'automatic_allowed':False}}) is expected


def test_both_jev_passes_exclude_foreign_jobs_before_classifying(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client)
        d.configure(Settings(enabled=False,auto_assess=False))
        monkeypatch.setattr('app.discovery.BOARDS',{'yc':'YC'})
        rows=[{**row('Own product strategy. '*20),'source_id':country,
               'url':'https://example.com/jobs/'+country,'location':country}
              for country in ('USA','India','Remote')]
        d.collector=lambda _: (rows,'Partial coverage',[])
        d.complete_descriptions=lambda _: {}
        classified=[];screened=[]
        monkeypatch.setattr(d.jev,'run',lambda items,stop: classified.extend(i['location'] for i in items) or {})
        monkeypatch.setattr('app.discovery_screening.screen',lambda engine,items,profile,stop: screened.extend(i['location'] for i in items) or {})
        d.scan()
        assert set(classified)=={'USA','Remote'}
        assert set(screened)=={'USA','Remote'}


def test_automatic_new_input_version_runs_once_and_changed_jd_keeps_old_job(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client);d.configure(Settings(enabled=False,auto_assess=True))
        oid,_=d.save('yc',row('Responsibilities version one. '*20))
        version=['a'*64];attempts=[]
        monkeypatch.setattr(d.analysis,'view',lambda job:{'run':None,'ready':True,'previous_result_outdated':True,'input_version':version[0]})
        monkeypatch.setattr(d.analysis,'start',lambda job,data:attempts.append((job.id,data.input_version)))
        first=d.import_role(oid,automatic=True)
        assert first['assessment_started']
        assert not d.import_role(oid,automatic=True).get('assessment_started')
        version[0]='b'*64
        assert d.import_role(oid,automatic=True)['assessment_started']
        d.save('yc',row('Responsibilities version two. '*20));version[0]='c'*64
        second=d.import_role(oid,automatic=True)
        assert second['job_id']!=first['job_id'] and second['assessment_started']
        assert d.service.store.read(first['job_id'])[0].description.startswith('Responsibilities version one')
        assert len(attempts)==3


def test_saved_high_priority_reconciles_without_new_scan(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client);d.configure(Settings(enabled=False,auto_assess=True))
        oid,_=d.save('yc',row('Responsibilities '*30))
        item={'id':oid,'keyword_match':{'priority':'high','label':'High priority'}}
        with d.db() as db: db.execute('INSERT INTO automatic_candidates VALUES(?)',(oid,))
        monkeypatch.setattr(d,'view',lambda **kw:{'items':[item]})
        calls=[];monkeypatch.setattr(d,'assess_high_priority',lambda ids,**kw:calls.append(ids))
        d.reconcile_automatic()
        assert calls==[{oid}]
        with d.db() as db:
            assert db.execute('SELECT COUNT(*) FROM scans').fetchone()[0]==0
            assert db.execute('SELECT opportunity_id FROM automatic_candidates').fetchone()[0]==oid


def test_unexpected_queue_failure_pauses_automatic_work_visibly(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client);d.configure(Settings(enabled=False,auto_assess=True))
        def fail(**kw):raise RuntimeError('test failure')
        monkeypatch.setattr(d,'view',fail)
        d.reconcile_automatic()
        assert not d.settings()['auto_assess']
        assert 'stopped unexpectedly' in client.app.state.job_list.status()['error']


def test_description_timeout_continues_to_next_listing(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        discovery = configure(client)
        first, _ = discovery.save('yc', row())
        second, _ = discovery.save('yc', {**row(), 'source_id':'second', 'url':'https://example.com/jobs/second'})
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith('/pm'):
                raise ExtractionError('Full JD fetching timed out. The scan continued.')
            return {'title':'Product Manager', 'company':'Example Labs',
                    'description':'Research customers and deliver useful products. '*20}
        monkeypatch.setattr('app.description_worker.extract_bounded', fetch)
        counts = discovery.complete_descriptions({first, second})
        assert len(calls) == 2
        assert counts == {'fetched':1, 'needs_paste':1}
        with discovery.db() as db:
            error = db.execute('SELECT error FROM description_fetches WHERE opportunity_id=?', (first,)).fetchone()[0]
        assert 'timed out' in error


def test_identical_description_new_source_reuses_role(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        d=configure(client)
        item=row('Own customer discovery and product delivery. '*15)
        first,new=d.save('yc',item)
        assert new
        second,new=d.save('yc',{**item,'source_id':'repost','url':'https://example.com/repost'})
        assert second==first and not new
        with d.db() as db:
            assert db.execute('SELECT count(*) FROM opportunities').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM sightings').fetchone()[0]==2
        separate,new=d.save('yc',{**item,'source_id':'other','url':'https://example.com/other','location':'London'})
        assert separate!=first and new
