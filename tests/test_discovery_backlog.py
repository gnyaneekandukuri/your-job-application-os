import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.discovery_screening import inputs
from scripts.process_discovery_backlog import preview, main


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup', headers={'X-Local-Review':'1'}, json={'countries':['US'], 'target_roles':['Product Manager']})
        yield client.app.state.discovery


def save(d, name, days=0, description=True, host='example.com'):
    oid, _ = d.save('lenny', dict(source_id=name, title='Product Manager '+name, company='Example',
                    location='USA', url=f'https://{host}/jobs/{name}', description=('Own customer discovery and product delivery. '*12 + name) if description else ''))
    with d.db() as db:
        db.execute('UPDATE opportunities SET first_seen=? WHERE id=?', ((datetime.now(timezone.utc)-timedelta(days=days)).isoformat(), oid))
    return oid


def test_pending_selection_active_newest_and_bounded(discovery):
    d = discovery
    old = save(d, 'old', 8)
    active = save(d, 'active', 2)
    newest = save(d, 'newest')
    passed = save(d, 'passed')
    missing = save(d, 'missing', description=False)
    with d.db() as db: db.execute('INSERT INTO passed_jobs VALUES(?,?)',(passed, datetime.now(timezone.utc).isoformat()))
    profile = d.screening_profile({'location_filter':{'countries':['US']}})
    assert d.backlog_ids('screening', 2, profile) == [newest, active]
    assert d.backlog_ids('screening', 10, profile) == [newest, active, old]
    assert d.backlog_ids('description', 10) == [missing]
    item = next(i for i in d.view(include_analysis=False)['items'] if i['id'] == newest)
    d.analysis.engine.put(inputs(item, profile)[1], {'state':'error'})
    assert newest not in d.backlog_ids('screening',10,profile)  # Explicit retry needed for provider errors.


def test_scan_screens_backlog_absent_from_source_results(discovery, monkeypatch):
    d = discovery
    oid = save(d, 'backlog')
    d.collector = lambda board: ([], 'checked', [])
    monkeypatch.setattr(d, 'complete_descriptions', lambda ids: {})
    monkeypatch.setattr(d.jev, 'run', lambda *a: {'state':'complete'})
    screened = []
    monkeypatch.setattr('app.discovery_screening.screen', lambda engine, items, profile, stop: screened.extend(i['id'] for i in items) or {'errors':0})
    d.scan(['lenny'])
    assert screened == [oid]


def test_description_rate_limit_cap_and_cooldown(discovery, monkeypatch):
    d = discovery
    ids = {save(d, str(n), description=False) for n in range(3)}
    monkeypatch.setattr('app.discovery.DESCRIPTION_LIMIT',2)
    waits = []
    monkeypatch.setattr(d.stop, 'wait', lambda seconds: waits.append(seconds) or False)
    monkeypatch.setattr('app.description_worker.extract_bounded', lambda url: (_ for _ in ()).throw(ValueError('unverified')))
    counts = d.complete_descriptions(ids)
    assert counts == dict(fetched=0, needs_paste=2, attempted=2, deferred=1)
    assert len(waits) == 1 and 4 <= waits[0] <= 5
    remaining = d.backlog_ids('description', 50)
    assert len(remaining) == 1
    counts = d.complete_descriptions(ids)
    assert counts['attempted'] == 1


def test_different_hosts_do_not_wait_and_success_is_saved(discovery, monkeypatch):
    d = discovery
    a = save(d, 'a', description=False, host='a.example.com')
    b = save(d, 'b', description=False, host='b.example.com')
    monkeypatch.setattr(d.stop, 'wait', lambda seconds: pytest.fail('Different hosts should not wait'))
    def extract(url):
        name = url.rsplit('/',1)[-1]
        return dict(title='Product Manager '+name, company='Example', description='Build useful products with customers. '*15)
    monkeypatch.setattr('app.description_worker.extract_bounded', extract)
    counts = d.complete_descriptions({a,b})
    assert counts['fetched'] == 2
    assert not d.backlog_ids('description', 50)


def test_explicit_backlog_batch_does_not_collect_or_deep_assess(discovery, monkeypatch):
    d = discovery
    oid = save(d, 'pending')
    monkeypatch.setattr(d, 'collector', lambda *a: pytest.fail('Backlog must not collect boards'))
    monkeypatch.setattr(d, 'start_automatic', lambda: pytest.fail('Backlog must not dispatch deep assessments'))
    calls = []
    monkeypatch.setattr('app.discovery_screening.screen', lambda engine, items, profile, stop: calls.extend(i['id'] for i in items) or {'errors':0})
    d.process_backlog()
    assert calls == [oid]
    with d.db() as db:
        assert not db.execute('SELECT * FROM automatic_candidates').fetchone()
        assert json.loads(db.execute('SELECT payload FROM scans').fetchone()[0])['backlog_only']


def test_backlog_endpoint_guard_and_scan_exclusion(discovery, monkeypatch):
    d = discovery
    d.scan_lock.acquire()
    try:
        with pytest.raises(Exception, match='already running'): d.start_backlog()
    finally: d.scan_lock.release()


def test_dry_run_default_read_only_no_network_or_app_initialization(tmp_path, monkeypatch, capsys):
    path = tmp_path/'job-list.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE snapshot (id INTEGER,updated TEXT)')
        db.execute("INSERT INTO snapshot VALUES(1,'snapshot-time')")
        db.execute('CREATE TABLE listings (payload TEXT)')
        for label, reason in [('Not screened','Semantic matching is pending the next scan.'), ('Needs information','Add the full job description to match this role.')]:
            db.execute('INSERT INTO listings VALUES(?)', (json.dumps(dict(first_seen=datetime.now(timezone.utc).isoformat(), keyword_match=dict(label=label,reason=reason))),))
    before = path.read_bytes()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('Dry run made a network call'))
    monkeypatch.setattr('app.main.create_app', lambda *a: pytest.fail('Dry run initialized the app'))
    main(['--data-dir',str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert report['counts'] == dict(pending=1,pending_active=1,missing_description=1,missing_description_active=1)
    assert report['full_backlog_estimate']['estimated_usd'] > 0
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['job-list.sqlite3']


def test_dry_run_missing_database_does_not_create_it(tmp_path):
    with pytest.raises(sqlite3.OperationalError): preview(tmp_path)
    assert not list(tmp_path.iterdir())
