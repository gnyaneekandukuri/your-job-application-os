import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import httpx
import pytest
from fastapi.testclient import TestClient

from app.discovery_recovery import lookback, source_window, source_metrics, ScanDeadline
from app.discovery_sources import fetch
from app.discovery_network import BoardClient
from app.main import create_app
from app.store import Conflict


class Wait:
    def __init__(self): self.delays = []
    def is_set(self): return False
    def wait(self, seconds): self.delays.append(seconds); return False


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup', headers={'X-Local-Review':'1'}, json={'countries':['US'], 'target_roles':['Product Manager']})
        yield client.app.state.discovery


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
def test_transient_status_retry_with_jitter_and_retry_after(monkeypatch, status):
    monkeypatch.setattr('app.discovery_network.uniform', lambda *a: 1.2)
    requests = []
    wait = Wait()
    def respond(request):
        requests.append(request)
        return httpx.Response(status if len(requests) == 1 else 200, text='ok', headers={'Retry-After':'15'})
    with BoardClient(stop=wait, transport=httpx.MockTransport(respond)) as client:
        assert fetch(client, 'https://example.com/jobs') == 'ok'
    assert wait.delays == [15]
    assert len(requests) == 2


def test_retry_after_date_and_long_delay_not_retried(monkeypatch):
    from app.discovery_network import retry_after
    from email.utils import format_datetime
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45))
    assert 43 < retry_after(httpx.Response(429, headers={'Retry-After':value})) <= 45
    wait = Wait()
    with BoardClient(stop=wait, transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={'Retry-After':'600'}))) as client:
        with pytest.raises(httpx.HTTPStatusError) as error: fetch(client, 'https://example.com/jobs')
    assert not wait.delays
    assert error.value.discovery_diagnostic['retry_after_seconds'] == 600


@pytest.mark.parametrize('gap,days', [(6,1), (26,7), (200,30), (900,30)])
def test_lookback_covers_gap_with_cap(gap, days):
    current = datetime(2030, 1, 1, tzinfo=timezone.utc)
    window = lookback('linkedin', (current - timedelta(hours=gap)).isoformat(), current)
    assert window['days'] == days
    assert window['capped'] == (gap > 720)
    assert lookback('yc', None, current)['label'] == 'full directory'
    assert lookback('lenny', None, current)['label'] == 'All (bounded pagination)'


def test_failed_source_does_not_advance_success_watermark(discovery):
    d = discovery
    current = datetime.now(timezone.utc)
    with d.db() as db:
        for hours, state in [(50,'checked'), (2,'error')]:
            db.execute('INSERT INTO scans(started,finished,payload) VALUES(?,?,?)',
                       ((current-timedelta(hours=hours)).isoformat(), current.isoformat(), json.dumps({'sources':{'linkedin':{'state':state}}})))
    assert d.source_lookback('linkedin')['days'] == 7
    assert 'Catch up' in d.source_lookback('linkedin')['reason']


def test_linkedin_wider_window_keeps_older_jobs(monkeypatch):
    from app.linkedin_source import collect_linkedin
    from tests.test_linkedin_source import CARD
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setattr('app.linkedin_source.PAGE_DELAY', 0)
    calls = []
    def read(client, url):
        calls.append(url)
        return 'Past week filter is currently applied' + CARD.replace('2 hours ago','3 days ago')
    token = source_window.set({'days':7})
    try:
        rows, coverage, warnings = collect_linkedin(None, read, ['US'])
    finally: source_window.reset(token)
    assert len(rows) == 1
    assert parse_qs(urlsplit(calls[0]).query)['f_TPR'] == ['r604800']
    assert 'Past week' in coverage


def test_browser_timeout_retries_but_login_does_not(discovery, monkeypatch):
    from app.yc_connection import YCConnectionError
    d = discovery
    wait = Wait()
    monkeypatch.setattr(d, 'active_deadline', wait)
    calls = []
    def collect(board):
        calls.append(board)
        if len(calls) < 3:
            error = YCConnectionError('error','Browser timeout')
            error.discovery_diagnostic['kind'] = 'TimeoutError'
            raise error
        return [], 'directory', []
    d.collector = collect
    token = source_metrics.set({'attempts':1})
    try:
        assert d.collect_with_recovery('yc') == ([], 'directory', [])
        assert source_metrics.get()['attempts'] == 3
        d.collector = lambda board: (_ for _ in ()).throw(YCConnectionError('login_required','Sign in again'))
        with pytest.raises(YCConnectionError): d.collect_with_recovery('yc')
    finally:
        source_metrics.reset(token)
        d.active_deadline = None
    assert len(calls) == 3 and len(wait.delays) == 2


def test_scan_lock_prevents_direct_and_ui_overlap(discovery, monkeypatch):
    d = discovery
    entered, release = Event(), Event()
    def collect(board):
        entered.set()
        release.wait(5)
        return [], 'checked', []
    d.collector = collect
    monkeypatch.setattr(d, 'complete_descriptions', lambda ids: {})
    worker = Thread(target=d.scan, args=(['yc'],))
    worker.start()
    try:
        assert entered.wait(2)
        with pytest.raises(Conflict): d.scan(['yc'])
        with pytest.raises(Conflict): d.start(['yc'])
        with d.db() as db: assert db.execute('SELECT count(*) FROM scans').fetchone()[0] == 1
    finally:
        release.set()
        worker.join(5)


def test_stale_record_abandoned_but_fresh_record_blocks(discovery):
    d = discovery
    current = datetime.now(timezone.utc)
    with d.db() as db:
        old = db.execute('INSERT INTO scans(started,payload) VALUES(?,?)', ((current-timedelta(hours=3)).isoformat(), '{}')).lastrowid
    fresh = d.claim_scan({'sources':{}})
    with d.db() as db:
        assert json.loads(db.execute('SELECT payload FROM scans WHERE id=?',(old,)).fetchone()[0])['state'] == 'abandoned'
    with pytest.raises(Conflict): d.claim_scan({})
    with d.db() as db: assert db.execute('SELECT finished FROM scans WHERE id=?',(fresh,)).fetchone()[0] is None


def test_sleep_expiry_fences_returning_collector(discovery, monkeypatch):
    d = discovery
    def collect(board):
        d.active_deadline.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        return [dict(source_id='expired')], 'checked', []
    d.collector = collect
    monkeypatch.setattr(d, 'complete_descriptions', lambda ids: pytest.fail('Expired scan continued'))
    d.scan(['yc'])
    with d.db() as db:
        result = json.loads(db.execute('SELECT payload FROM scans').fetchone()[0])
        assert result['state'] == 'abandoned'
        assert not db.execute('SELECT id FROM opportunities').fetchone()


def test_source_error_details_and_persistent_cooldown(discovery, monkeypatch, caplog):
    d = discovery
    calls = []
    def blocked(board):
        calls.append(board)
        request = httpx.Request('GET','https://example.com/jobs?private=hidden')
        raise httpx.HTTPStatusError('private body', request=request, response=httpx.Response(429,request=request,headers={'Retry-After':'900'}))
    d.collector = blocked
    monkeypatch.setattr(d, 'complete_descriptions', lambda ids: {})
    with caplog.at_level('INFO', logger='uvicorn.error.discovery'): d.scan(['linkedin'])
    d.scan(['linkedin'])
    assert len(calls) == 1
    with d.db() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM scans ORDER BY id')]
    source = rows[0]['sources']['linkedin']
    assert source['attempts'] == 1 and source['error_type'] == 'HTTPStatusError'
    assert source['started_at'] <= source['ended_at']
    assert source['lookback']['days'] == 7
    assert source['retry_not_before']
    assert rows[1]['sources']['linkedin']['state'] == 'deferred'
    assert rows[1]['sources']['linkedin']['attempts'] == 0
    assert 'private body' not in caplog.text and 'private=hidden' not in caplog.text


def test_builtin_detail_failures_preserve_catchup_watermark(discovery):
    d = discovery
    current = datetime.now(timezone.utc)
    with d.db() as db:
        for hours, warnings in [(50, []), (2, [{'listing':'example','reason':'HTTP 429'}])]:
            db.execute('INSERT INTO scans(started,finished,payload) VALUES(?,?,?)',
                ((current-timedelta(hours=hours)).isoformat(), current.isoformat(),
                 json.dumps({'sources':{'builtin':{'state':'checked', 'warnings':warnings}}})))
    assert d.source_lookback('builtin')['days'] == 7


def test_deadline_wait_never_passes_deadline():
    current = [datetime.now(timezone.utc)]
    wait = Wait()
    deadline = ScanDeadline(wait, 10, clock=lambda:current[0])
    deadline.wait(100)
    assert wait.delays == [10]
    current[0] += timedelta(hours=11)
    assert deadline.is_set()
