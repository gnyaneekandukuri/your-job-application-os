from fastapi.testclient import TestClient
from app.main import create_app

H={'X-Local-Review':'1'}
BODY={'company':'4M Analytics','title':'Senior Forward Deployed Product Manager','applied_date':'2026-10-17'}


def test_external_application_is_listed_by_date_without_duplicates(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        first=client.post('/api/applications/external',json={**BODY,'url':'https://example.com/jobs/1/'},headers=H)
        assert first.status_code==200, first.text
        again=client.post('/api/applications/external',json={**BODY,'url':'https://example.com/jobs/1'},headers=H)
        assert again.json()['id']==first.json()['id']
        client.post('/api/applications/external',json={**BODY,'title':'Product Manager'},headers=H)
        rows=client.get('/api/applications').json()
        assert len(rows)==2
        row=next(r for r in rows if r['url'])
        assert (row['company'],row['applied_date'],row['status'],row['url'])==('4M Analytics','2026-10-17','Applied','https://example.com/jobs/1')


def test_external_application_rejects_bad_link(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post('/api/applications/external',json={**BODY,'url':'not a link'},headers=H).status_code==422
