from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from app.discovery_sources import fetch
from app.discovery_network import BoardClient
from app.discovery import scan_summary
from app.main import create_app


class InstantWait:
    def __init__(self): self.delays = []
    def is_set(self): return False
    def wait(self, seconds): self.delays.append(seconds); return False


@pytest.fixture(autouse=True)
def deterministic_jitter(monkeypatch):
    monkeypatch.setattr('app.discovery_network.uniform', lambda low, high: low)


def test_sleep_wake_connection_recovers_without_duplicate_requests():
    stop, requests = InstantWait(), []
    def handler(request):
        requests.append(request)
        if len(requests) < 3: raise httpx.ConnectTimeout('offline', request=request)
        return httpx.Response(200, text='jobs')
    with BoardClient(stop=stop, transport=httpx.MockTransport(handler)) as client:
        assert fetch(client, 'https://example.com/jobs') == 'jobs'
        assert client.recovered_requests == 1
    assert stop.delays == [10, 20]
    assert len(requests) == 3


@pytest.mark.parametrize('status', [403, 999, 401])
def test_access_failures_are_not_retried(status):
    stop = InstantWait()
    with BoardClient(stop=stop, transport=httpx.MockTransport(lambda r:httpx.Response(status))) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            fetch(client, 'https://example.com/jobs?secret=hidden')
    diagnostic = error.value.discovery_diagnostic
    assert diagnostic['attempts'] == 1
    assert diagnostic['http_status'] == status
    assert 'secret' not in str(diagnostic)
    assert not stop.delays


def test_network_retries_are_bounded_and_cancelable():
    stop = InstantWait()
    def handler(request): raise httpx.ConnectError('private message', request=request)
    with BoardClient(stop=stop, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError) as error: fetch(client, 'https://example.com/jobs')
    assert stop.delays == [10, 20, 40]
    assert error.value.discovery_diagnostic['attempts'] == 4
    assert 'private message' not in str(error.value.discovery_diagnostic)
    canceled = Event(); canceled.set()
    with BoardClient(stop=canceled, transport=httpx.MockTransport(lambda r:pytest.fail('Request after stop'))) as client:
        with pytest.raises(RuntimeError): fetch(client, 'https://example.com/jobs')


def test_server_retry_after_is_respected():
    stop = InstantWait()
    with BoardClient(stop=stop, transport=httpx.MockTransport(lambda r:httpx.Response(503,headers={'Retry-After':'300'}))) as client:
        with pytest.raises(httpx.HTTPStatusError): fetch(client, 'https://example.com/jobs')
    assert not stop.delays


def test_all_failed_scan_is_not_complete(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        discovery = client.app.state.discovery
        def fail(board): raise httpx.ConnectTimeout('offline',request=httpx.Request('GET','https://example.com/jobs'))
        discovery.collector = fail
        discovery.complete_descriptions = lambda ids: {'fetched':0, 'needs_paste':0}
        discovery.scan()
        with discovery.db() as db:
            import json
            result = json.loads(db.execute('SELECT payload FROM scans').fetchone()[0])
        assert result['state'] == 'failed'
        from app.discovery_sources import BOARDS
        assert result['coverage'] == {'responded':0,'total':len(BOARDS)}
        assert result['sources']['yc']['diagnostic']['kind'] == 'ConnectTimeout'
    assert scan_summary({'state':'complete','sources':{'yc':{'state':'blocked'}}})['state']=='failed'
    assert scan_summary({'state':'complete','sources':{'yc':{'state':'checked'}}})['state']=='incomplete'
