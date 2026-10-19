import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.lenny_connection import LennyConnection, LennyConnectionError, save_status
from app.lenny_browser_worker import ready, result_page, collect_pages
from app.store import Conflict


def test_missing_session_and_exclusive_profile(tmp_path):
    c = LennyConnection(tmp_path)
    assert c.status()['state'] == 'NOT_CONNECTED'
    with pytest.raises(LennyConnectionError): c.collect(['US'])
    (c.root / 'profile').mkdir(parents=True)
    save_status(c.root, 'SCANNING', 'Collecting')
    assert c.status()['state'] == 'NEEDS_CHECK'
    c.lock.acquire()
    try:
        with pytest.raises(Conflict): c.start()
        with pytest.raises(LennyConnectionError): c.collect(['US'])
    finally: c.lock.release()


def test_country_options_passed_to_worker(tmp_path, monkeypatch):
    c = LennyConnection(tmp_path)
    (c.root / 'profile').mkdir(parents=True)
    def run(command):
        assert command == 'scan'
        assert json.loads((c.root / 'scan-options.json').read_text()) == {'countries': ['US']}
        return dict(rows=[], coverage='Verified empty', warnings=[])
    monkeypatch.setattr(c, '_run', run)
    assert c.collect(['US']) == ([], 'Verified empty', [])


class Control:
    enabled = True
    checked = True
    def count(self): return 1
    def is_enabled(self): return self.enabled
    def is_checked(self): return self.checked
    def click(self): pass


class Page:
    url = 'https://www.lennysjobs.com/'
    def __init__(self): self.radio = Control()
    def get_by_role(self, role, **kwargs): return self.radio if role == 'radio' else Control()


def data(ids, total):
    return {'results': [{'nbHits': total, 'hits': [dict(job_id=i, title='Product Manager',
                url=f'https://example.com/jobs/{i}', company_name='Example', updated_at='2026-10-18') for i in ids]}]}


def test_locked_filter_cannot_confirm_connection():
    p = Page(); assert ready(p)
    p.radio.enabled = False; assert not ready(p)
    p.radio.enabled = True; p.url = 'https://example.com/'; assert not ready(p)


def test_all_pages_deduplicated_and_update_time_not_posting_date(monkeypatch):
    monkeypatch.setattr('app.lenny_browser_worker.response_for', lambda *a: data(['1','2'], 2))
    rows,total,loaded,warnings = collect_pages(Page(), data(['1'], 2))
    assert (total,loaded,warnings) == (2,2,[])
    assert len(rows) == 2 and rows[0]['posted_at'] is None
    assert rows[0]['date_basis'] == 'source_updated_at'


def test_verified_empty_and_repeated_page(monkeypatch):
    assert collect_pages(Page(), data([], 0)) == ([],0,0,[])
    monkeypatch.setattr('app.lenny_browser_worker.response_for', lambda *a: data(['1'], 2))
    assert collect_pages(Page(), data(['1'], 2))[3]
    with pytest.raises(ValueError): result_page({'results': [{'hits': []}]})


def test_expired_access_rejects_results():
    p = Page(); p.radio.checked = False
    with pytest.raises(ValueError): collect_pages(p, data(['1'], 1))


def test_endpoints_and_collector_dispatch(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        d = client.app.state.discovery
        seen = []
        monkeypatch.setattr(d.lenny, 'start', lambda cmd: seen.append(cmd) or {'state':'CONNECTING'})
        headers = {'X-Local-Review':'1'}
        assert client.post('/api/discovery/lenny/connect', headers=headers).status_code == 200
        assert client.post('/api/discovery/lenny/check', headers=headers).status_code == 200
        assert seen == ['login','check']
        monkeypatch.setattr(d, 'start', lambda boards: {'boards':boards})
        assert client.post('/api/discovery/lenny/scan', headers=headers).json() == {'boards':['lenny']}
        monkeypatch.setattr('app.discovery.load_preferences', lambda *a: {'location_filter':{'countries':['US']}})
        monkeypatch.setattr(d.lenny, 'collect', lambda countries: (countries,'coverage',[]))
        assert d.collect_board('lenny')[0] == ['US']
