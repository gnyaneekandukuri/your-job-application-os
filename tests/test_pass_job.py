from fastapi.testclient import TestClient
from app.main import create_app

H = {'X-Local-Review': '1'}
JD = 'Own the analytics roadmap and partner with engineering on governed metrics. ' * 5


def test_pass_hides_listing_from_new_jobs_and_automatic_analysis_and_can_be_undone(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup', headers=H, json={'target_roles': ['Product Manager'], 'countries': ['US']})
        discovery = client.app.state.discovery
        oid, _ = discovery.save('yc', {'source_id': 'one', 'title': 'Product Manager', 'company': 'Example Labs',
                                       'url': 'https://example.com/jobs/pm', 'location': 'New York, NY, USA',
                                       'description': JD, 'posted_at': None})
        item = lambda: next(i for i in discovery.view()['items'] if i['id'] == oid)
        assert item()['passed'] is False
        assert client.post(f'/api/discovery/{oid}/pass', headers=H, json={'passed': True}).json() == {'passed': True}
        passed = item()
        assert passed['passed'] and not discovery.auto_eligible({**passed, 'keyword_match': {'priority': 'high'}})
        client.post(f'/api/discovery/{oid}/pass', headers=H, json={'passed': False})
        assert item()['passed'] is False
        assert client.post('/api/discovery/missing/pass', headers=H, json={'passed': True}).status_code == 404
