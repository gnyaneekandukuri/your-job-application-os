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


@pytest.mark.parametrize('warning',[{'kind':'ReadTimeout','reason':'Response timed out'},'Search page: HTTP 429','LinkedIn limited requests after 2 pages.','One hiring comment could not be read; coverage is partial.'])
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
        def handoff_persisted():
            assert not d.lock._is_owned()
            with d.db() as db:
                assert db.execute('SELECT finished FROM scans ORDER BY id DESC LIMIT 1').fetchone()[0]
                assert db.execute('SELECT opportunity_id FROM automatic_candidates').fetchone()
        kick=Mock(side_effect=handoff_persisted)
        monkeypatch.setattr(d,'start_automatic',kick)
        original=d.view;calls=[]
        def view(**kw):
            calls.append(kw)
            result=original(**kw)
            for item in result['items']:
                item.update(keyword_match={'priority':'high','label':'High priority'},assessment={'state':'NOT_STARTED'})
            return result
        monkeypatch.setattr(d,'view',view)
        d.scan(['yc'])
        kick.assert_called_once()
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
def test_restart_script_skips_busy_or_newly_busy_server(initial_busy,tmp_path):
    import shutil,subprocess
    from pathlib import Path
    shell=shutil.which('powershell.exe')
    if not shell: pytest.skip('Windows restart script')
    script=Path('scripts/restart-review-server.ps1').resolve()
    acknowledged='$true' if initial_busy else '$false'
    command=rf"""
    $env:JOB_REVIEW_DATA_DIR='{tmp_path}'
    $global:checks=0
    function Get-NetTCPConnection {{ [pscustomobject]@{{OwningProcess=123}} }}
    function Invoke-RestMethod {{
      $global:checks++
      [pscustomobject]@{{safe_to_restart=$false;draining={acknowledged};active=@('scan')}}
    }}
    function Get-CimInstance {{ [pscustomobject]@{{ParentProcessId=456;CommandLine='{script.parent.parent}\.venv\Scripts\python.exe -m uvicorn app.main:app'}} }}
    function Stop-Process {{ throw 'TEST FAILURE: stopped active work' }}
    function Start-Process {{ throw 'TEST FAILURE: launched over active work' }}
    & '{script}' -DrainTimeoutSeconds 0
    """
    result=subprocess.run([shell,'-NoProfile','-Command',command],capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stderr
    assert 'Restart skipped' in result.stdout
    assert 'TEST FAILURE' not in result.stdout
    assert not (tmp_path/'maintenance.flag').exists()

@pytest.mark.parametrize('warning', [
    {'listing':'one','reason':'HTTP 404'},
    'Job 123: HTTP 404; description unavailable.',
    'Job 123: TimeoutError; description unavailable.',
])
def test_listing_issues_are_aggregated_information(warning):
    payload={'state':'incomplete','phase':'incomplete','boards':['yc'],
             'sources':{'yc':{'state':'checked','warnings':[warning]}}}
    result=scan_summary(payload)
    assert result['state']==result['phase']=='complete'
    assert result['sources']['yc']['listing_issues']==1
    assert result['sources']['yc']['notes']==[{'severity':'info','reason':'1 listings need review'}]
    assert not warning_is_error(warning)


def test_failed_scan_stays_failed_without_responses():
    result=scan_summary({'state':'failed','phase':'failed','boards':['yc'],
                        'sources':{'yc':{'state':'error','error':'HTTP 429'}}})
    assert result['state']==result['phase']=='failed'


def test_snapshot_endpoints_are_compact_and_never_build_view(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        jl=client.app.state.job_list
        jl.close()
        with jl.connect() as db:
            db.execute('INSERT OR REPLACE INTO snapshot VALUES(1,?,?,?,?)',
                       ('test','revision','today',json.dumps({'run_reports':[{'body':'x'*2280000}],
                        'assessment_results':{'body':'x'*480000},'settings':{}})))
        monkeypatch.setattr(d,'view',lambda **kw:pytest.fail('HTTP rebuilt discovery view'))
        response=client.get('/api/discovery')
        assert response.status_code==200
        assert len(response.content)<50000
        assert not {'run_reports','assessment_results'} & response.json().keys()
        assert client.get('/api/discovery/page?runs=true').status_code==200


@pytest.mark.parametrize('phase',['descriptions','classifying'])
def test_assessment_skips_unstable_scan_and_drain(tmp_path,monkeypatch,phase):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        client.app.state.job_list.close()
        with d.db() as db:
            db.execute('INSERT INTO scans(started,payload) VALUES(?,?)',('today',json.dumps({'phase':phase})))
        monkeypatch.setattr(d,'view',lambda **kw:pytest.fail('Must skip before reading listings'))
        d.assess_high_priority({'one'})
        with d.db() as db: db.execute("UPDATE scans SET finished='done'")
        (tmp_path/'maintenance.flag').touch()
        d.assess_high_priority({'one'})
        assert not d.scheduled_scan(datetime(2030,1,1,13))
        assert client.get('/api/maintenance/status').json()['draining']
        with pytest.raises(Exception,match='draining'): d.start(['yc'])


def test_reconcile_scopes_to_candidates_and_pastes(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        client.app.state.job_list.close()
        with d.db() as db:
            db.execute("INSERT INTO automatic_candidates VALUES('tracked')")
            for oid,method in [('pasted','user_paste'),('other','board')]:
                db.execute('INSERT INTO opportunities(id,payload,first_seen,last_seen) VALUES(?,?,?,?)',
                           (oid,json.dumps({'description_provenance':{'method':method}}),'now','now'))
        view=Mock(return_value={'items':[]}); monkeypatch.setattr(d,'view',view)
        monkeypatch.setattr(d,'assess_high_priority',Mock())
        d.reconcile_automatic()
        view.assert_called_once_with(opportunity_ids={'tracked','pasted'})


def test_queued_candidate_attention_preserves_other_reasons():
    from app.scan_overview import blocker
    item={'automatic_candidate':True,'description':'x'*200,'location_match':{'status':'match'}}
    assert blocker(item)[0]=='Queued for automatic deep analysis'
    item['description_changed']=True
    assert 'description changed' in blocker(item)[0]


def test_scan_consolidation_serializes_import_with_consistent_lock_order(tmp_path,monkeypatch):
    from threading import RLock, local
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        state=local()
        class OrderedLock:
            def __init__(self, name): self.name,self.inner=name,RLock()
            def __enter__(self):
                if self.name=='import':
                    assert not getattr(state,'discovery',0) or getattr(state,'import',0)>0
                self.inner.acquire()
                setattr(state,self.name,getattr(state,self.name,0)+1)
                return self
            def __exit__(self,*args):
                setattr(state,self.name,getattr(state,self.name)-1)
                self.inner.release()
        d.lock,d.import_lock=OrderedLock('discovery'),OrderedLock('import')
        oid,_=d.save('yc',dict(source_id='one',company='Example',title='PM',
            description='Own product discovery and delivery. '*20,url='https://example.com/one'))
        d.collector=lambda board:([],'complete',[])
        monkeypatch.setattr(d,'complete_descriptions',lambda ids:{})
        monkeypatch.setattr(d,'view',lambda **kw:{'items':[]})
        monkeypatch.setattr(d.jev,'run',lambda *a:{'state':'complete'})
        monkeypatch.setattr(d,'screening_profile',lambda p:{})
        monkeypatch.setattr('app.discovery_screening.screen',lambda *a:{'errors':0})
        entered,release,imported=Event(),Event(),Event()
        errors=[]
        def consolidate(db):
            assert getattr(state,'import',0)>0 and getattr(state,'discovery',0)>0
            entered.set(); assert release.wait(5)
            return []
        monkeypatch.setattr('app.job_dedup.consolidate',consolidate)
        def importing():
            try: d.import_role(oid,analyze=False); imported.set()
            except Exception as exc: errors.append(exc)
        scan=Thread(target=d.scan,args=(['yc'],)); scan.start()
        try:
            assert entered.wait(2)
            importer=Thread(target=importing); importer.start()
            assert not imported.wait(.1)
        finally: release.set()
        scan.join(5); importer.join(5)
        assert not scan.is_alive() and not importer.is_alive()
        assert imported.is_set() and not errors


def test_drain_stops_automatic_between_jobs(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        d.configure(Settings(enabled=False,auto_assess=True))
        items=[dict(id=str(n),title='PM '+str(n),company='Example',description='Product roadmap '+str(n)*300,
                    keyword_match={'priority':'high'},location_match={'status':'match'},assessment={'state':'NOT_STARTED'}) for n in range(2)]
        calls=[]
        def importing(oid,**kw):
            calls.append(oid)
            (tmp_path/'maintenance.flag').touch()
            return {}
        monkeypatch.setattr(d,'import_role',importing)
        monkeypatch.setattr(d,'record_scan_role',lambda *a,**kw:None)
        monkeypatch.setattr(d,'view',lambda **kw:{'items':[i for i in items if i['id'] in kw['opportunity_ids']]})
        d.assess_high_priority({'0','1'},items=items)
        assert calls==['0']
        assert not d.queued_ids
        assert client.get('/api/maintenance/status').json()['safe_to_restart']


def test_drain_preserves_manual_queue_without_dispatch(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        q=client.app.state.manual_queue
        q.close()
        q.stop.clear()
        q.engine.put('manual-analysis-queue:one',{'job_id':'one','state':'QUEUED','created_at':'now'})
        (tmp_path/'maintenance.flag').touch()
        monkeypatch.setattr(q.analysis,'start',lambda *a:pytest.fail('Dispatched while draining'))
        q.step()
        assert q.pending()
        assert client.get('/api/maintenance/status').json()['safe_to_restart']


def test_restart_script_drains_then_restarts_unique_pid_and_cleans_flag(tmp_path):
    import shutil,subprocess
    from pathlib import Path
    shell=shutil.which('powershell.exe')
    if not shell: pytest.skip('Windows restart script')
    script=Path('scripts/restart-review-server.ps1').resolve()
    command=rf"""
    $env:JOB_REVIEW_DATA_DIR='{tmp_path}'
    $global:checks=0; $global:stopped=0; $global:started=0
    function Get-NetTCPConnection {{ @([pscustomobject]@{{OwningProcess=123}},[pscustomobject]@{{OwningProcess=123}}) }}
    function Get-CimInstance {{ [pscustomobject]@{{ParentProcessId=456;CommandLine='{script.parent.parent}\.venv\Scripts\python.exe -m uvicorn app.main:app'}} }}
    function Invoke-RestMethod {{
        $global:checks++
        [pscustomobject]@{{safe_to_restart=($global:checks -gt 1);draining=(Test-Path '{tmp_path}\maintenance.flag');active=@()}}
    }}
    function Start-Sleep {{ }}
    function Stop-Process {{ param($Id)
        if ($Id -ne 123 -or $global:checks -lt 2) {{ throw 'TEST FAILURE: stopped before drain' }}
        $global:stopped++
    }}
    function Wait-Process {{ }}
    function Get-Process {{ }}
    function Start-Process {{
        if (-not (Test-Path '{tmp_path}\maintenance.flag')) {{ throw 'TEST FAILURE: missing drain during restart' }}
        $global:started++
    }}
    & '{script}'
    Write-Output "stopped=$global:stopped started=$global:started checks=$global:checks"
    """
    result=subprocess.run([shell,'-NoProfile','-Command',command],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'Restart skipped' not in result.stdout,result.stdout
    assert 'stopped=1 started=1 checks=3' in result.stdout
    assert not (tmp_path/'maintenance.flag').exists()



def test_historical_listing_error_is_display_only_and_interrupted_phase_matches():
    payload={'state':'incomplete','phase':'incomplete','boards':['yc'],
             'sources':{'yc':{'state':'error','error':'Some collection requests failed.',
                               'warnings':['Job 123: HTTP 404; description unavailable.']}}}
    before=copy.deepcopy(payload)
    result=scan_summary(payload)
    assert result['state']==result['phase']=='complete'
    assert result['sources']['yc']['state']=='checked'
    assert payload==before
    assert scan_summary({'state':'interrupted','phase':'descriptions'})['phase']=='interrupted'


@pytest.mark.parametrize('recovers', [True, False])
def test_restart_script_retries_startup_and_reports_stopped_server(tmp_path, recovers):
    import shutil, subprocess
    from pathlib import Path
    shell = shutil.which('powershell.exe')
    if not shell:
        pytest.skip('Windows restart script')
    script = Path('scripts/restart-review-server.ps1').resolve()
    recovery = '$true' if recovers else '$false'
    command = rf"""
    $env:JOB_REVIEW_DATA_DIR='{tmp_path}'
    $global:stopped=0; $global:started=0; $global:ticks=0
    function Get-NetTCPConnection {{ [pscustomobject]@{{OwningProcess=123}} }}
    function Get-CimInstance {{ [pscustomobject]@{{ParentProcessId=456;CommandLine='{script.parent.parent}\.venv\Scripts\python.exe -m uvicorn app.main:app'}} }}
    function Get-Date {{ $global:ticks++; [datetime]::new(2026,10,21).AddSeconds($global:ticks * 2) }}
    function Invoke-RestMethod {{
        if ($global:stopped -eq 0) {{ return [pscustomobject]@{{safe_to_restart=$true;draining=$true}} }}
        if ({recovery} -and $global:started -eq 2) {{ return [pscustomobject]@{{safe_to_restart=$false;draining=$true}} }}
        throw 'Mock startup not healthy'
    }}
    function Stop-Process {{ $global:stopped++ }}
    function Wait-Process {{ }}
    function Get-Process {{ }}
    function Start-Sleep {{ }}
    function Start-Process {{
        if (-not (Test-Path '{tmp_path}\maintenance.flag')) {{ throw 'TEST FAILURE: flag missing' }}
        $global:started++
        Write-Output "mock-start=$global:started"
    }}
    & '{script}' -StartTimeoutSeconds 1
    """
    result = subprocess.run([shell, '-NoProfile', '-Command', command],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == (0 if recovers else 1), result.stdout + result.stderr
    assert 'mock-start=2' in result.stdout
    assert 'Server stopped; replacement not healthy yet' in result.stdout
    assert 'Restart skipped' not in result.stdout
    if not recovers:
        assert 'after two startup attempts' in result.stdout
    assert not (tmp_path/'maintenance.flag').exists()


def test_light_runs_and_settings_realistic(tmp_path, monkeypatch):
    from time import perf_counter
    with TestClient(create_app(tmp_path)) as client:
        d = client.app.state.discovery
        monkeypatch.setattr(d, 'view', Mock(side_effect=AssertionError('full view called')))
        role = {'id': 'role', 'company': 'Example', 'title': 'Product manager',
                'reason': 'Evidence ' * 500, 'assessment': {'state': 'COMPLETE'}}
        payload = {'state': 'complete', 'boards': ['yc'], 'tracking_version': 1,
                   'sources': {'yc': {'state': 'checked', 'count': 200, 'coverage': 'Directory',
                                       'warnings': ['Job x: description unavailable.'] * 200}}}
        with d.db() as db:
            for sid in range(1, 76):
                db.execute('INSERT INTO scans VALUES(?,?,?,?)',
                           (sid, '2026-10-21', '2026-10-21', json.dumps(payload)))
                db.executemany('INSERT INTO scan_roles VALUES(?,?,?)',
                    [(sid, str(n), json.dumps({**role, 'id': str(n)})) for n in range(200)])
            for n in range(200):
                db.execute('INSERT INTO opportunities(id,payload,first_seen,last_seen) VALUES(?,?,?,?)',
                           (str(n), json.dumps(role), '2026-10-21', '2026-10-21'))
        jl = client.app.state.job_list
        with jl.connect() as db:
            db.execute('INSERT OR REPLACE INTO snapshot VALUES(1,?,?,?,?)',
                ('stale', 'revision', 'today', json.dumps({
                    'run_reports': [{'body': 'x' * 2280000}],
                    'assessment_results': {'body': 'x' * 480000},
                    'board_counts': {'yc': 200},
                    'keyword_profile': {'experience': ['x' * 500000],
                                        'target': {'roles': ['PM'], 'keywords': [], 'version': 'v1'}}})))
        start = perf_counter()
        response = client.get('/api/discovery/page?runs=true')
        elapsed = perf_counter() - start
        data = response.json()
        assert response.status_code == 200
        assert len(response.content) < 200000 and elapsed < 1
        assert data['board_counts'] == {'yc': 200}
        assert data['unavailable'] is not None
        assert data['keyword_profile']['experience'] == []
        assert len(data['run_reports']) == 10 and data['total'] == 75
        assert data['run_reports'][0]['id'] == 75
        assert 'roles' not in data['run_reports'][0]
        assert 'warnings' not in data['run_reports'][0]['sources']['yc']
        assert data['run_reports'][0]['counts']['tracked'] == 200
        assert client.get('/api/discovery/page?view=runs&runs_page=2&runs_limit=3').json()['run_reports'][0]['id'] == 72
        assert jl.page(runs=True)['run_reports'][0]['id'] == 75
        start_detail = perf_counter()
        detail = client.get('/api/discovery/runs/75')
        detail_elapsed = perf_counter() - start_detail
        assert detail.status_code == 200 and len(detail.json()['roles']) == 200
        assert detail.json()['sources']['yc']['coverage'] == 'Directory'
        assert client.get('/api/discovery/runs/999').status_code == 404
        d.pause_on_provider_limit('OpenAI HTTP 429')
        with jl.connect() as db:
            db.execute('UPDATE snapshot SET fingerprint=? WHERE id=1', (jl.fingerprint(),))
        assert not jl.status()['refreshing']
        start = perf_counter()
        saved = client.post('/api/discovery/settings', json={'enabled': False, 'auto_assess': False},
                            headers={'X-Local-Review': '1'})
        settings_elapsed = perf_counter() - start
        assert saved.status_code == 200 and settings_elapsed < 1
        assert saved.json()['settings']['auto_assess_pause_reason']
        assert jl.status()['refreshing']
        enabled = d.configure(Settings(enabled=False, auto_assess=True))
        assert 'auto_assess_pause_reason' not in enabled['settings']
        assert d.settings() == enabled['settings']
        print(f'BENCH runs={len(response.content)} bytes {elapsed:.4f}s; settings={len(saved.content)} bytes {settings_elapsed:.4f}s; detail={len(detail.content)} bytes {detail_elapsed:.4f}s')


def test_idle_analysis_lock_does_not_block_drain(tmp_path):
    from threading import Lock
    with TestClient(create_app(tmp_path)) as client:
        d = client.app.state.discovery
        d.analysis.lock = Lock()
        with d.analysis.lock:
            assert client.get('/api/maintenance/status').json()['safe_to_restart']
            d.analysis.worker = Mock(is_alive=lambda: True, join=lambda **kw: None)
            assert 'deep_analysis' in client.get('/api/maintenance/status').json()['active']
            d.analysis.worker = None


def test_resume_approach_drain_finishes_current_and_keeps_queue(tmp_path, monkeypatch):
    from app import resume_approach as mod
    from pydantic import BaseModel
    class Job(BaseModel):
        id: str
    with TestClient(create_app(tmp_path)) as client:
        q = client.app.state.resume_approach
        entered, release, second = Event(), Event(), Event()
        monkeypatch.setattr(q.discovery, 'annotate_handled', lambda items: None)
        monkeypatch.setattr(q.discovery, 'saved_assessment', lambda jid: {'state': 'COMPLETE'})
        monkeypatch.setattr(mod, 'inputs', lambda t, job: (job.id, {}))
        def evaluate(engine, job, key, body):
            if job.id == 'one':
                entered.set()
                assert release.wait(5)
            else:
                second.set()
            return {'state': 'COMPLETE'}
        monkeypatch.setattr(mod, 'evaluate', evaluate)
        q.queue.put((Job(id='one'), 'one', {}, 'stamp'))
        assert entered.wait(2)
        (tmp_path/'maintenance.flag').touch()
        q.queue.put((Job(id='two'), 'two', {}, 'stamp'))
        assert not client.get('/api/maintenance/status').json()['safe_to_restart']
        release.set()
        assert not second.wait(.3)
        assert q.queue.unfinished_tasks == 1
        assert client.get('/api/maintenance/status').json()['safe_to_restart']
        (tmp_path/'maintenance.flag').unlink()
        assert second.wait(2)
