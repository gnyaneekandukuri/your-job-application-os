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


def test_passing_an_unfinished_saved_role_hides_it_and_its_listing_and_can_be_undone(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup', headers=H, json={'target_roles': ['Product Manager'], 'countries': ['US']})
        discovery = client.app.state.discovery
        oid, _ = discovery.save('yc', {'source_id': 'three', 'title': 'Product Manager', 'company': 'Example Labs',
                                       'url': 'https://example.com/jobs/pm3', 'location': 'New York, NY, USA',
                                       'description': JD, 'posted_at': None})
        job_id = discovery.import_role(oid, analyze=False)['job_id']
        assert client.post(f'/api/assessments/{job_id}/pass', headers=H, json={'passed': True}).json() == {'passed': True}
        with discovery.db() as db:
            assert db.execute('SELECT 1 FROM passed_saved_jobs WHERE job_id=?', (job_id,)).fetchone()
            assert db.execute('SELECT 1 FROM passed_jobs WHERE opportunity_id=?', (oid,)).fetchone()
        client.post(f'/api/assessments/{job_id}/pass', headers=H, json={'passed': False})
        with discovery.db() as db:
            assert not db.execute('SELECT 1 FROM passed_saved_jobs WHERE job_id=?', (job_id,)).fetchone()
            assert not db.execute('SELECT 1 FROM passed_jobs WHERE opportunity_id=?', (oid,)).fetchone()
        assert client.post('/api/assessments/missing/pass', headers=H, json={'passed': True}).status_code == 404

def test_pass_does_not_wait_for_scan_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.job_retention import pass_job, pass_saved_job
    with TestClient(create_app(tmp_path)) as client:
        discovery = client.app.state.discovery
        oid, _ = discovery.save('yc', {'source_id': 'independent', 'title': 'Product Manager',
            'company': 'Example', 'url': 'https://example.com/jobs/independent',
            'location': 'USA', 'description': JD, 'posted_at': None})
        with ThreadPoolExecutor(max_workers=1) as pool:
            discovery.lock.acquire()
            try:
                assert pool.submit(pass_job, discovery, oid, True).result(timeout=3) == {'passed': True}
                assert pool.submit(pass_job, discovery, oid, False).result(timeout=3) == {'passed': False}
            finally:
                discovery.lock.release()

def test_writing_forms_do_not_reconcile_or_wait_for_scan(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    with TestClient(create_app(tmp_path)) as client:
        discovery = client.app.state.discovery
        oid, _ = discovery.save('yc', {'source_id': 'writing-fast', 'title': 'Product Manager',
            'company': 'Example', 'url': 'https://example.com/jobs/writing-fast',
            'location': 'USA', 'description': JD, 'posted_at': None})
        def unexpected(*args, **kwargs):
            raise AssertionError('Opening writing help must not reconcile analysis')
        monkeypatch.setattr(discovery.service, 'review', unexpected)
        with ThreadPoolExecutor(max_workers=1) as pool:
            discovery.lock.acquire()
            try:
                result = pool.submit(discovery.import_role, oid, analyze=False).result(timeout=3)
            finally:
                discovery.lock.release()
        job_id = result['job_id']
        assert not result['assessment_started']
        assert discovery.import_role(oid, analyze=False)['job_id'] == job_id
        assert client.get(f'/api/jobs/{job_id}/team-message').status_code == 200
        assert client.get(f'/api/jobs/{job_id}/application-answers').status_code == 200
