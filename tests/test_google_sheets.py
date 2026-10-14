import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.google_sheets import GoogleSheets, ClientConfig, SheetTarget, HEADERS, TAB, SCOPE, updates_for, application_row
from app.retrieval import RetrievalError


def row(key='app-1', **changes):
    return {'id':key,'company':'Example','title':'PM','applied_date':'2026-10-14','status':'Applied',
            'location':'Remote','url':'https://example.com/job/1','notes':'=IMPORTXML("x")',**changes}


def test_upsert_is_idempotent_and_tracks_sheet_row_reordering():
    first, second = row(), row('app-2')
    writes, added, changed = updates_for([first,second], [])
    assert added == 2 and changed == 0 and writes[0]['values'] == [HEADERS]
    saved = [HEADERS,application_row(second),application_row(first)]
    assert updates_for([first,second], saved) == ([],0,0)
    writes,added,changed=updates_for([row(status='Interviewing'),second,row('app-3')],saved)
    assert (added,changed)==(1,1)
    assert writes[0]['range'].endswith('A3:K3') and writes[1]['range'].endswith('A4:K4')


@pytest.mark.parametrize('saved', [[['Company','Role']], [HEADERS,application_row(row()),application_row(row())], [HEADERS,['','something']]])
def test_conflicting_headers_and_duplicate_or_missing_ids_stop_writes(saved):
    with pytest.raises(RetrievalError): updates_for([row()],saved)


def configured(tmp_path, handler=lambda req:httpx.Response(500)):
    service=GoogleSheets(tmp_path,SimpleNamespace(list=lambda:[row()]),httpx.MockTransport(handler))
    service.configure(ClientConfig(credentials_json=json.dumps({'installed':{'client_id':'test.apps.googleusercontent.com','client_secret':'test-secret'}})))
    data=service.read();data['tokens']={'access_token':'test-access','refresh_token':'test-refresh','expires_at':time.time()+3600}
    data['target']={'id':'sheet-test','title':'Tracker','url':'https://docs.google.com/spreadsheets/d/sheet-test/edit'}
    service.write(data)
    return service


def test_oauth_uses_state_pkce_and_never_exposes_credentials(tmp_path):
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh','scope':SCOPE,'expires_in':3600})
    s=configured(tmp_path,handler)
    params=parse_qs(urlparse(s.connect()['url']).query)
    assert params['code_challenge_method']==['S256'] and len(params['code_challenge'][0])==43
    with pytest.raises(RetrievalError):s.callback('wrong-state','code')
    assert calls==[]
    s.callback(params['state'][0],'code')
    assert 'code_verifier=' in calls[0].content.decode()
    assert s.status()['connected'] and not s.status()['target']
    assert 'new-access' not in json.dumps(s.status()) and 'test-secret' not in json.dumps(s.status())
    with pytest.raises(RetrievalError):s.callback(params['state'][0],'code')
    s.disconnect()
    assert not s.status()['connected'] and s.status()['configured']


def test_expired_or_denied_auth_does_not_exchange_code(tmp_path):
    s=configured(tmp_path,lambda req:pytest.fail('No exchange expected'))
    s.connect();s.pending['expires']=0
    with pytest.raises(RetrievalError):s.callback(s.pending['state'],'code')
    s.connect()
    with pytest.raises(RetrievalError):s.callback(s.pending['state'],'','access_denied')


def test_sync_retries_uncertain_write_without_duplicate_rows(tmp_path):
    saved=[];writes=[];uncertain=True
    def handler(req):
        nonlocal saved,uncertain
        if req.method=='GET' and '/values/' not in req.url.path:
            return httpx.Response(200,json={'sheets':[{'properties':{'title':TAB,'sheetId':7,'gridProperties':{'rowCount':1000,'columnCount':26}}}]})
        if req.method=='GET':return httpx.Response(200,json={'values':saved})
        body=json.loads(req.content);writes.append(body)
        assert body['valueInputOption']=='RAW'
        saved=[HEADERS,application_row(row())]
        if uncertain:
            uncertain=False
            raise httpx.ReadTimeout('unknown outcome')
        return httpx.Response(200,json={})
    s=configured(tmp_path,handler)
    with pytest.raises(RetrievalError,match='did not confirm'):s.sync()
    assert not s.status()['last_sync']
    result=s.sync()
    assert len(writes)==1 and len(saved)==2
    assert result['last_sync']['added']==0 and result['last_sync']['total']==1


def test_sync_creates_dedicated_tab_and_does_not_touch_other_tabs(tmp_path):
    calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET' and '/values/' not in req.url.path:return httpx.Response(200,json={'sheets':[{'properties':{'title':'Personal notes','sheetId':1}}]})
        if req.method=='GET':return httpx.Response(200,json={})
        body=json.loads(req.content)
        if 'requests' in body:
            assert body['requests'][0]['addSheet']['properties']['title']==TAB
            return httpx.Response(200,json={'replies':[{'addSheet':{'properties':{'title':TAB,'sheetId':9}}}]})
        assert all(TAB in data['range'] for data in body['data'])
        return httpx.Response(200,json={})
    s=configured(tmp_path,handler)
    assert s.sync()['last_sync']['added']==1
    assert len(calls)==4


def test_refresh_and_safe_errors(tmp_path):
    def handler(req):
        if req.url.host=='oauth2.googleapis.com':return httpx.Response(200,json={'access_token':'refreshed','expires_in':3600})
        assert req.headers['authorization']=='Bearer refreshed'
        return httpx.Response(403,json={'error':'secret provider detail'})
    s=configured(tmp_path,handler);data=s.read();data['tokens']['expires_at']=0;s.write(data)
    with pytest.raises(RetrievalError,match='Google denied access') as exc:s.sync()
    assert 'secret provider detail' not in str(exc.value)
    assert s.read()['tokens']['refresh_token']=='test-refresh'


def test_destination_validation_does_not_send_tokens_to_supplied_host(tmp_path):
    s=configured(tmp_path,lambda req:pytest.fail('Invalid URL must not be fetched'))
    with pytest.raises(RetrievalError):s.set_target(SheetTarget(url='https://evil.example/spreadsheets/d/test'))
    with pytest.raises(RetrievalError):s.configure(ClientConfig(credentials_json='{"type":"service_account"}'))


def test_routes_require_local_header_and_status_has_no_secrets(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/applications/sheets').json()['configured'] is False
        assert client.post('/api/applications/sheets/connect').status_code==403
        assert client.post('/api/applications/sheets/sync',headers={'X-Local-Review':'1'}).status_code==409
        callback=client.get('/api/applications/sheets/callback?state=bad&code=bad')
        assert callback.status_code==409 and callback.headers['cache-control']=='no-store'
