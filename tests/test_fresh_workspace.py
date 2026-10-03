import json
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import create_app
from app.user_workspace import initialize, control_documents

HEADERS = {"X-Local-Review": "1"}


def test_fresh_install_and_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/setup').json()['complete'] is False
        assert client.get('/api/library').json()['sources'] == []
        assert client.get('/api/jobs').json() == []
        assert client.get('/api/usage').json() == {'calls': [], 'scans': []}
        discovery = client.get('/api/discovery').json()
        assert discovery['items'] == []
        assert discovery['settings']['enabled'] is False
        assert discovery['settings']['auto_assess'] is False
        assert client.get('/api/library/keywords').status_code == 200
        response = client.post('/api/setup', headers=HEADERS, json={
            'name': 'Test Candidate', 'target_roles': ['Data Product Manager'], 'countries': ['ZA']})
        assert response.status_code == 200, response.text
        assert response.json()['requires_h1b_transfer'] is None
        assert client.get('/api/discovery').json()['location_preference']['countries'] == ['ZA']
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/setup').json()['name'] == 'Test Candidate'
        assert client.get('/api/library').json()['sources'] == []


def test_workspaces_do_not_share_candidate_data(tmp_path):
    with TestClient(create_app(tmp_path/'a')) as first, TestClient(create_app(tmp_path/'b')) as second:
        first.post('/api/setup', headers=HEADERS, json={'name':'Candidate One','target_roles':['Product Manager'],'countries':['US']})
        assert second.get('/api/setup').json()['name'] == ''
        assert second.get('/api/setup').json()['countries'] == []
        result = first.post('/api/library/sources', headers=HEADERS, json={'request_id':str(uuid4()),'title':'Fictional project','employer':'Example Labs','project':'Dashboard','text':'I researched customer needs and prioritized dashboard improvements.'})
        assert result.status_code == 201, result.text
        assert len(first.get('/api/library').json()['sources']) == 1
        assert second.get('/api/library').json()['sources'] == []


def test_control_documents_have_no_resume_dependency(tmp_path):
    initialize(tmp_path)
    docs = control_documents(tmp_path)
    assert 'master' not in docs
    assert json.loads(docs['facts']) == {'facts': []}
    local = tmp_path/'criteria/evidence-rubric.json'
    local.write_text('{"refinements":["My explicit correction"]}')
    initialize(tmp_path)
    assert 'My explicit correction' in local.read_text()


def test_setup_rejects_invalid_country_and_cross_origin(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        data={'target_roles':['Product Manager'],'countries':['XX']}
        assert client.post('/api/setup',headers=HEADERS,json=data).status_code == 400
        assert not client.get('/api/setup').json()['complete']
        assert client.post('/api/setup',json=data).status_code == 403


def test_job_analysis_status_needs_no_personal_master(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        saved = client.post('/api/jobs', headers=HEADERS, json={
            'request_id':str(uuid4()), 'title':'Product Manager', 'company':'Example Labs',
            'description':'Own customer discovery and roadmap planning for a reporting product.'})
        assert saved.status_code == 201, saved.text
        job_id = saved.json()['job']['id']
        view = client.get(f'/api/jobs/{job_id}/analysis')
        assert view.status_code == 200, view.text
        assert view.json()['ready'] is False
        pack = client.get('/api/library/context-pack')
        assert pack.status_code == 200, pack.text
        assert not pack.json()['reference_documents']


def test_resume_path_cannot_escape_workspace(tmp_path):
    import pytest
    initialize(tmp_path)
    (tmp_path/'knowledge/canonical_resume_inventory.json').write_text('{"source":"../outside.pdf"}')
    with pytest.raises(ValueError):
        control_documents(tmp_path)
