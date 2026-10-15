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

@pytest.mark.parametrize('state,protected',[('COMPLETE',False),('NEEDS_ATTENTION',False),('QUEUED',True),('RUNNING',True)])
def test_only_active_selections_protect_old_listing(tmp_path,state,protected):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        oid,_=d.save('yc',dict(source_id='one',company='Example',title='Product Manager',
                             location='USA',url='https://example.com/job/one',description=JD))
        with d.db() as db:db.execute('INSERT INTO selected_assessments VALUES(?,?,?,?)',(oid,'hash',state,''))
        assert d.view(include_analysis=False)['items'][0]['retention_protected'] is protected
