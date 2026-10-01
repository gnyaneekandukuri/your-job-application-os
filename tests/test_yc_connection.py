import json
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.yc_connection import YCConnection, YCConnectionError, save_status
from app.yc_browser_worker import job_description, directory_ready
from app.store import Conflict


def test_missing_session_does_not_silently_fall_back(tmp_path):
    connection = YCConnection(tmp_path)
    assert connection.status()['state'] == 'NOT_CONNECTED'
    with pytest.raises(YCConnectionError) as error: connection.collect()
    assert error.value.source_state == 'login_required'
    assert not (tmp_path / 'yc-browser/profile').exists()


def test_exclusive_profile_and_interrupted_status(tmp_path):
    connection = YCConnection(tmp_path)
    (connection.root / 'profile').mkdir(parents=True)
    save_status(connection.root,'SCANNING','Collecting')
    assert connection.status()['state'] == 'NEEDS_CHECK'
    connection.lock.acquire()
    try:
        assert connection.status()['busy']
        with pytest.raises(Conflict): connection.start()
        with pytest.raises(YCConnectionError) as error: connection.collect()
        assert error.value.source_state == 'busy'
    finally:
        connection.lock.release()


def test_login_status_requires_worker_verification(tmp_path,monkeypatch):
    connection = YCConnection(tmp_path)
    def login(command):
        assert command == 'login'
        save_status(connection.root,'LOGIN_REQUIRED','Sign in again')
        raise YCConnectionError('login_required','Sign in again')
    monkeypatch.setattr(connection,'_run',login)
    connection.start()
    connection.thread.join(timeout=2)
    assert connection.status()['state'] == 'LOGIN_REQUIRED'
    assert not connection.status()['busy']


def test_description_excludes_navigation_and_other_jobs():
    body = 'Own the product roadmap and work with engineers. ' * 8
    text = 'My profile\nJane\nAbout Example\nCompany\nAbout the role\n'+body+'\nOther jobs at Example\nAnother role\n'
    assert job_description(text) == body.strip()
    assert job_description('Login\nMy profile\nNo role description') == ''


def test_session_verification_requires_directory_and_authenticated_navigation():
    class Locator:
        def __init__(self,count,text=''): self.n,self.text=count,text
        def count(self): return self.n
        def inner_text(self): return self.text
    class Page:
        url='https://www.workatastartup.com/companies?role=product'
        signed_in=True
        def locator(self, selector):
            return Locator(int(self.signed_in),'Showing 148 matching startups')
    page=Page()
    assert directory_ready(page)
    page.signed_in=False
    assert not directory_ready(page)
    page.signed_in=True;page.url='https://account.ycombinator.com/magic'
    assert not directory_ready(page)


def test_auth_failure_preserves_listings_and_other_sources(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        row={'source_id':'123','url':'https://www.ycombinator.com/companies/example/jobs/pm',
             'company':'Example','title':'Product Manager','location':'US','description':'Existing evidence '*30}
        oid,_=d.save('yc',row)
        monkeypatch.setattr('app.discovery.BOARDS',{'yc':'YC','a16z':'a16z'})
        def collect(board):
            if board=='yc': raise YCConnectionError('login_required','Sign in again')
            return [],'Checked',[]
        d.collector=collect
        d.complete_descriptions=lambda ids:{'fetched':0,'needs_paste':0}
        d.scan()
        with d.db() as db:
            scan=json.loads(db.execute('SELECT payload FROM scans').fetchone()[0])
            saved=json.loads(db.execute('SELECT payload FROM opportunities WHERE id=?',(oid,)).fetchone()[0])
        assert scan['state']=='incomplete'
        assert scan['sources']['yc']['state']=='login_required'
        assert scan['sources']['a16z']['state']=='checked'
        assert saved['description']==row['description']
        # Same YC numeric ID across domains keeps one opportunity and original history.
        second,new=d.save('yc',{**row,'url':'https://www.workatastartup.com/jobs/123'})
        assert second==oid and not new


def test_yc_actions_require_local_header(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        called=[]
        monkeypatch.setattr(client.app.state.discovery.yc,'start',lambda action:called.append(action) or {'state':'CONNECTING'})
        assert client.post('/api/discovery/yc/connect').status_code==403
        assert not called
        assert client.post('/api/discovery/yc/connect',headers={'X-Local-Review':'1'}).status_code==200
        assert called==['login']


def test_zero_during_loading_is_not_an_empty_directory():
    from app.yc_browser_worker import directory_settled
    assert not directory_settled({'loaded':0,'total':0,'loading':False},1)
    assert not directory_settled({'loaded':0,'total':0,'loading':True},20)
    assert not directory_settled({'loaded':0,'total':None,'loading':False},20)
    assert directory_settled({'loaded':10,'total':148,'loading':True},0)
    assert directory_settled({'loaded':0,'total':0,'loading':False},10)


def test_single_source_scan_does_not_call_other_boards(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        calls=[]
        d.collector=lambda board: (calls.append(board) or [], 'Checked', [])
        d.complete_descriptions=lambda ids:{'fetched':0,'needs_paste':0}
        d.scan(['yc'])
        assert calls==['yc']
        with d.db() as db:
            result=json.loads(db.execute('SELECT payload FROM scans').fetchone()[0])
        assert result['state']=='complete'
        assert result['coverage']=={'responded':1,'total':1}


def test_dashboard_summaries_do_not_block_scan_writes(tmp_path,monkeypatch):
    import sqlite3
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        row={'source_id':'one','url':'https://www.workatastartup.com/jobs/1',
             'company':'Example','title':'Product Manager','location':'US','description':'Description '*30}
        d.save('yc',row)
        def summary(item):
            with sqlite3.connect(d.path,timeout=0.1) as db:
                db.execute('INSERT INTO scans(started,payload) VALUES(?,?)',('test','{}'))
            return {'state':'NOT_STARTED','score':None}
        monkeypatch.setattr(d,'assessment_summary',summary)
        assert len(d.view()['items'])==1


def test_company_description_heading_does_not_break_job_identity():
    from app.yc_browser_worker import job_heading_matches
    row={'title':'Product Manager, Marketplace','company':'HUD'}
    assert job_heading_matches(['Product Manager, Marketplace at HUD(W25)','About HUD'],row)
    assert not job_heading_matches(['Product Manager, Marketplace at Other','About HUD'],row)


def test_recent_description_cache_does_not_refresh_its_own_age():
    from datetime import datetime, timezone, timedelta
    from app.yc_browser_worker import cached_description
    now=datetime.now(timezone.utc)
    row={'title':'Product Manager','company':'Example','url':'https://www.workatastartup.com/jobs/1'}
    stamp=(now-timedelta(hours=1)).isoformat()
    saved={**row,'description':'Verified responsibilities '*20,'description_checked_at':stamp}
    assert cached_description(row,saved,now)==(saved['description'],stamp)
    assert cached_description(row,saved,now+timedelta(hours=4)) is None
    assert cached_description({**row,'company':'Other'},saved,now) is None
