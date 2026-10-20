from threading import Event
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.discovery import Discovery
from app.store import Conflict

JD = 'Own the product roadmap and customer discovery with engineering. ' * 8

def eligible(**changes):
    return dict(id='one', company='Example', title='Product Manager', description=JD,
                keyword_match={'priority':'high'}, location_match={'status':'match'},
                assessment={'state':'STALE'}, job_id='saved', **changes)

def test_refresh_existing_once_without_importing_duplicate():
    d = object.__new__(Discovery)
    original = eligible()
    duplicate = {**original, 'id':'two', 'job_id':None, 'assessment':{'state':'NOT_STARTED'}}
    d.view = lambda: {'items':[duplicate,original]}
    d.stop=Event(); d.analysis=SimpleNamespace(worker=None); d.queued_ids=set()
    d.settings=lambda:{'auto_assess':True}
    d.record_scan_role=lambda *a,**k:None
    calls=[]
    d.import_role=lambda oid,**k:calls.append(oid)
    d.assess_high_priority({'one','two'})
    assert calls==['one']

@pytest.mark.parametrize('changes', [dict(applied=True),dict(passed=True),dict(assessment={'state':'STALE','choice':'SKIP'})])
def test_handled_roles_not_eligible(changes):
    item=eligible();item.update(changes)
    assert not Discovery.auto_eligible(item)

def test_saved_decisions_and_application_block_dispatch(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        oid,_=d.save('yc',dict(source_id='one',company='Example',title='Product Manager',
                             location='USA',url='https://example.com/job/one',description=JD))
        jid=d.import_role(oid,analyze=False)['job_id']
        d.analysis.engine.put('pursuit-decision-event:test',dict(job_id=jid,choice='SKIP',created_at='2026-10-14'))
        with pytest.raises(Conflict,match='already applied or passed'):
            d.import_role(oid,automatic=True)
        duplicate={'id':'other-board','company':'Example','title':'Product Manager','description':JD}
        different={**duplicate,'id':'different-role','title':'Engineering Manager'}
        d.annotate_handled([duplicate,different])
        assert duplicate['passed'] and not different['passed']
        d.analysis.engine.put('pursuit-decision-event:reconsider',dict(job_id=jid,choice='CASUAL',created_at='2026-10-15'))
        item={'id':oid,'job_id':jid,'company':'Example','title':'Product Manager','description':JD}
        d.annotate_handled([item]);assert not item['passed']
        import sqlite3,json
        with sqlite3.connect(tmp_path/'applications.sqlite3') as db:
            db.execute('INSERT INTO applications VALUES(?,?,?)',('application',jid,json.dumps({'job_id':jid,'url':'https://example.com/job/one','company':'Example','title':'Product Manager'})))
        with pytest.raises(Conflict,match='already applied or passed'):
            d.import_role(oid,automatic=True)
        other_id, _ = d.save('yc', dict(source_id='separate-opening', company='Example',
            title='Product Manager', location='USA', url='https://example.com/job/two',
            description=JD + ' This is a separate opening on the payments team.'))
        items = d.view(include_analysis=False)['items']
        applied = next(i for i in items if i['id'] == oid)
        separate = next(i for i in items if i['id'] == other_id)
        assert applied['applied'] and not d.initial_screening_eligible(applied)
        assert not d.auto_eligible(applied)
        assert not separate['applied'] and not separate['passed']
        assert d.initial_screening_eligible(separate)

@pytest.mark.parametrize('state,protected',[('COMPLETE',False),('NEEDS_ATTENTION',False),('QUEUED',True),('RUNNING',True)])
def test_only_active_selections_protect_old_listing(tmp_path,state,protected):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        oid,_=d.save('yc',dict(source_id='one',company='Example',title='Product Manager',
                             location='USA',url='https://example.com/job/one',description=JD))
        with d.db() as db:db.execute('INSERT INTO selected_assessments VALUES(?,?,?,?)',(oid,'hash',state,''))
        assert d.view(include_analysis=False)['items'][0]['retention_protected'] is protected

def test_pasted_jd_is_screened_without_waiting_for_a_scan(monkeypatch):
    d = object.__new__(Discovery)
    pasted = dict(id='pasted', description=JD, keyword_match={'label':'Not screened'}, location_match={'status':'match'},
                  description_provenance={'method':'user_paste'})
    other = {**pasted, 'id':'other', 'description_provenance':{'method':'listing_extraction'}}
    screened = []
    monkeypatch.setattr('app.discovery_screening.screen', lambda engine, items, profile, stop: screened.extend(i['id'] for i in items))
    monkeypatch.setattr('app.discovery.load_preferences', lambda path: {})
    d.view = lambda: {'items':[pasted, other]}
    d.screening_profile = lambda prefs: {}
    d.analysis = SimpleNamespace(engine=None, library=SimpleNamespace(root=__import__('pathlib').Path('.')))
    d.stop = Event(); d.queued_ids = set()
    class DB:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a): return []
        def executemany(self, *a): pass
    d.db = DB
    d.assess_high_priority = lambda ids: None
    d.reconcile_automatic()
    assert screened == ['pasted']
