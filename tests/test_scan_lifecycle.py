import copy
import json
import sqlite3
from datetime import datetime
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.discovery import scan_summary, warning_is_error, Settings
from app.retrieval import RetrievalService


def test_partial_is_information_and_history_is_unchanged():
    payload={'state':'incomplete','boards':['yc'],'sources':{'yc':{'state':'partial','coverage':'Partial directory','warnings':['Scrolling limit reached.']}}}
    before=copy.deepcopy(payload)
    result=scan_summary(payload)
    assert result['state']=='complete'
    assert result['sources']['yc']['coverage_status']=='partial'
    assert result['sources']['yc']['state']=='checked'
    assert payload==before


@pytest.mark.parametrize('warning',[{'kind':'ReadTimeout','reason':'Response timed out'},'Job 123: HTTP 429; description unavailable.','Job 123: ValueError; description needs review.','LinkedIn limited requests after 2 pages.','One hiring comment could not be read; coverage is partial.'])
def test_real_warning_failures_remain_incomplete(warning):
    assert warning_is_error(warning)
    result=scan_summary({'state':'complete','boards':['linkedin'],'sources':{'linkedin':{'state':'checked','warnings':[warning]}}})
    assert result['state']=='incomplete'


@pytest.mark.parametrize('warning',['Reached five-page limit; coverage is partial','Recovered 2 requests after temporary connection failures.','Skipped 10 reposted jobs.'])
def test_coverage_and_recovered_errors_are_not_failures(warning):
    assert not warning_is_error(warning)


@pytest.mark.parametrize('field,value',[('classification',{'state':'error','error':'Invalid Jev response'}),('screening',{'errors':1})])
def test_screening_errors_affect_completion(field,value):
    assert scan_summary({'state':'complete','boards':['yc'],'sources':{'yc':{'state':'checked'}},field:value})['state']=='incomplete'


def test_scan_scheduling_and_explicit_duplicate_start(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        d.configure(Settings(enabled=True,auto_assess=True))
        hold=Event();entered=Event()
        def scan(*a): entered.set();hold.wait(5)
        monkeypatch.setattr(d,'scan',scan)
        monkeypatch.setattr(d,'view',lambda **kw:pytest.fail('Start must not rebuild listings'))
        d.assessment_worker=Mock(is_alive=lambda:True,join=lambda **kw:None)
        d.selected.worker=Mock(is_alive=lambda:True,join=lambda **kw:None)
        try:
            assert d.scheduled_scan(datetime(2030,1,1,13,0))
            assert entered.wait(1)
            assert d.settings()['last_slot']=='2030-01-01T13'
            assert not d.scheduled_scan(datetime(2030,1,1,20,0))
            assert d.settings()['last_slot']=='2030-01-01T13'
            response=client.post('/api/discovery/check',headers={'X-Local-Review':'1'})
            assert response.status_code==409
            assert 'already running' in response.json()['detail']
            status=client.get('/api/discovery/status').json()
            assert status['running'] and status['automatic_running']
        finally:
            hold.set();d.worker.join(2)
        assert not client.get('/api/discovery/status').json()['running']
        assert client.get('/api/discovery/status').json()['automatic_running']
        assert d.scheduled_scan(datetime(2030,1,1,20,0))
        d.worker.join(2)


def test_scan_persists_handoff_without_waiting_and_keeps_attribution(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        client.post('/api/setup',headers={'X-Local-Review':'1'},json={'countries':['US'],'target_roles':['PM']})
        d.configure(Settings(enabled=False,auto_assess=True))
        d.collector=lambda board:([dict(source_id='one',company='Example',title='PM',description='Own product discovery and delivery. '*20,location='USA',url='https://example.com/one')],'Partial directory',[])
        monkeypatch.setattr(d,'complete_descriptions',lambda ids:{})
        monkeypatch.setattr(d.jev,'run',lambda *a:{'state':'complete'})
        monkeypatch.setattr('app.discovery_screening.screen',lambda *a:{'errors':0})
        monkeypatch.setattr(d,'assess_high_priority',lambda *a,**kw:pytest.fail('Scan dispatched deep analysis'))
        original=d.view;calls=[]
        def view(**kw):
            calls.append(kw)
            result=original(**kw)
            for item in result['items']:
                item.update(keyword_match={'priority':'high','label':'High priority'},assessment={'state':'NOT_STARTED'})
            return result
        monkeypatch.setattr(d,'view',view)
        d.scan(['yc'])
        assert len(calls)==2 and all('opportunity_ids' in k for k in calls)
        with d.db() as db:
            scan=db.execute('SELECT * FROM scans').fetchone()
            payload=json.loads(scan['payload'])
            assert scan['finished'] and payload['state']=='complete'
            assert payload['assessments_queued']==1
            oid=db.execute('SELECT opportunity_id FROM automatic_candidates').fetchone()[0]
            assert db.execute('SELECT scan_id FROM automatic_origins WHERE opportunity_id=?',(oid,)).fetchone()[0]==scan['id']
        # Background updates retain origin even though the scan request is over.
        d.record_scan_role({'id':oid},assessment={'state':'COMPLETE'},assessment_requested=True)
        with d.db() as db:
            role=json.loads(db.execute('SELECT payload FROM scan_roles WHERE scan_id=?',(scan['id'],)).fetchone()[0])
            assert role['assessment']['state']=='COMPLETE'
            assert db.execute('SELECT finished FROM scans').fetchone()[0]==scan['finished']


def test_latest_decisions_backfill_and_atomic_updates(tmp_path):
    path=tmp_path/'cache.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE cache(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        for n,choice in [(1,'SKIP'),(2,'CASUAL')]:
            db.execute('INSERT INTO cache VALUES(?,?)',(f'pursuit-decision-event:{n}',json.dumps({'job_id':'job','choice':choice,'created_at':str(n)})))
    engine=RetrievalService(path,tmp_path/'source',None)
    engine.put('pursuit-decision-event:3',{'job_id':'job','choice':'SKIP','created_at':'3'})
    engine.put('pursuit-decision-event:old',{'job_id':'job','choice':'CASUAL','created_at':'0'})
    with sqlite3.connect(path) as db:
        assert json.loads(db.execute('SELECT payload FROM latest_pursuit_decisions WHERE job_id=?',('job',)).fetchone()[0])['choice']=='SKIP'
        assert db.execute('SELECT COUNT(*) FROM latest_pursuit_decisions').fetchone()[0]==1
        plan=db.execute('EXPLAIN QUERY PLAN SELECT payload FROM latest_pursuit_decisions WHERE job_id=?',('job',)).fetchone()[-1]
        assert 'SEARCH' in plan


def test_overview_does_not_rebuild_and_restart_refuses_active_work(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        monkeypatch.setattr(d,'table_view',lambda **kw:pytest.fail('HTTP rebuilt all jobs'))
        assert client.get('/api/discovery').status_code==200
        d.worker=Mock(is_alive=lambda:True,join=lambda **kw:None)
        response=client.get('/api/maintenance/status').json()
        assert not response['safe_to_restart'] and 'scan' in response['active']
        d.worker=None
        assert client.get('/api/maintenance/status').json()['safe_to_restart']


def test_writing_request_blocks_restart(tmp_path):
    from app.application_answer import LOCK
    with TestClient(create_app(tmp_path)) as client:
        with LOCK:
            response=client.get('/api/maintenance/status').json()
            assert not response['safe_to_restart'] and 'application_answer' in response['active']


@pytest.mark.parametrize('initial_busy',[True,False])
def test_restart_script_skips_busy_or_newly_busy_server(initial_busy):
    import shutil,subprocess
    from pathlib import Path
    shell=shutil.which('powershell.exe')
    if not shell: pytest.skip('Windows restart script')
    script=Path('scripts/restart-review-server.ps1').resolve()
    first='$false' if initial_busy else '$true'
    command=rf"""
    $script:checks=0
    function Get-NetTCPConnection {{ [pscustomobject]@{{OwningProcess=123}} }}
    function Invoke-RestMethod {{
      $script:checks++
      [pscustomobject]@{{safe_to_restart=($(if($script:checks -eq 1){{{first}}}else{{$false}}));active=@('scan')}}
    }}
    function Get-CimInstance {{ [pscustomobject]@{{ParentProcessId=456;CommandLine='{script.parent.parent}\.venv\Scripts\python.exe -m uvicorn app.main:app'}} }}
    function Stop-Process {{ throw 'TEST FAILURE: stopped active work' }}
    function Start-Process {{ throw 'TEST FAILURE: launched over active work' }}
    & '{script}'
    """
    result=subprocess.run([shell,'-NoProfile','-Command',command],capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stderr
    assert 'Restart skipped' in result.stdout
