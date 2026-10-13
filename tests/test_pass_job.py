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


def test_saving_a_replacement_description_is_fast_and_retires_the_old_unfinished_analysis(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup', headers=H, json={'target_roles': ['Product Manager'], 'countries': ['US']})
        discovery = client.app.state.discovery
        oid, _ = discovery.save('yc', {'source_id': 'two', 'title': 'Product Manager', 'company': 'Example Labs',
                                       'url': 'https://example.com/jobs/pm2', 'location': 'New York, NY, USA',
                                       'description': JD, 'posted_at': None})
        job_id = discovery.import_role(oid, analyze=False)['job_id']
        with discovery.db() as db:
            db.execute('UPDATE opportunities SET imported_hash=? WHERE id=?', ('older-version', oid))
        detail = client.get(f'/api/discovery/{oid}/detail').json()
        saved = client.post(f'/api/discovery/{oid}/description', headers=H, json={
            'expected_hash': detail['content_hash'], 'title': 'Product Manager', 'company': 'Example Labs',
            'description': JD + ' Lead discovery with design partners.'})
        assert saved.status_code == 200 and saved.json()['saved'] is True and 'items' not in saved.json()
        with discovery.db() as db:
            assert db.execute('SELECT 1 FROM superseded_jobs WHERE job_id=?', (job_id,)).fetchone()
            assert db.execute('SELECT job_id FROM opportunities WHERE id=?', (oid,)).fetchone()[0] is None
