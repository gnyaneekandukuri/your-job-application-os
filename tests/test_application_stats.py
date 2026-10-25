import json

from fastapi.testclient import TestClient
from app.main import create_app


def record(index, status='Applied', created='2026-10-21T07:30:00Z', **extra):
    return dict(id=str(index), job_id='external:'+str(index), status=status,
                created_at=created, applied_date='2026-10-25', **extra)


def fetch_stats(tmp_path, records, priorities=None):
    with TestClient(create_app(tmp_path)) as client:
        with client.app.state.applications.db() as db:
            for row in records:
                db.execute('INSERT INTO applications VALUES (?,?,?)',
                           (row['id'], row['job_id'], json.dumps(row)))
        if priorities:
            client.app.state.discovery.saved_assessment = lambda job_id: priorities[job_id]
        response = client.get('/api/applications/stats')
        assert response.status_code == 200, response.text
        return response.json()


def test_endpoint_maps_every_supported_status_once(tmp_path):
    stats = fetch_stats(tmp_path, [record(i, status) for i, status in enumerate(
        ['Applied', 'Rejected', 'Interviewing', 'Offer', 'Withdrawn', 'Legacy'])])
    assert [(r['label'], r['count']) for r in stats['funnel']] == [
        ('Applied', 6), ('Rejected', 1), ('No response', 1), ('Screen', 0), ('Interview', 2)]
    assert stats['funnel'][1]['percent'] == 16.7
    assert stats['approaches'][1]['screens'] == 2
    assert stats['approaches'][1]['screen_rate'] == 33.3
    assert stats['approaches'][1]['rejection_rate'] == 16.7
    assert stats['withdrawn'] == 1
    assert stats['unknown_statuses'] == {'Legacy': 1}


def test_endpoint_resume_cutoff_and_tailoring_precedence(tmp_path):
    stats = fetch_stats(tmp_path, [
        record(0, created='2026-10-21T07:29:59+00:00'),
        record(1, recorded_without_resume=True),
        record(2, created='2026-10-21T02:30:00-05:00'),
        record(3, created='2026-10-20T00:00:00Z', resume_revision='tailored-v1'),
        record(4, resume_snapshot={'draft': {}}),
        record(5, tailoring_revision='revision-2'),
    ])
    assert [r['applied'] for r in stats['approaches']] == [3, 2, 1, 0, 0]


def test_endpoint_priorities_monday_buckets_and_missing_weeks(tmp_path):
    rows = [record(0, 'Interviewing', job_snapshot={'priority': 'Attack Hard'}),
            record(1, 'Rejected', job_snapshot={'recommendation': 'CASUAL'}), record(2)]
    rows[0]['applied_date'] = '2026-10-11'  # Sunday
    rows[1]['applied_date'] = '2026-10-12'  # Monday
    rows[2]['job_id'] = 'saved-job'
    rows[2]['applied_date'] = '2026-11-01'
    stats = fetch_stats(tmp_path, rows, {'saved-job': {'choice': 'ATTACK_HARD', 'recommendation': 'CASUAL'}})
    assert stats['approaches'][3] == dict(label='Attack Hard', applied=2, screens=1, screen_rate=50, rejection_rate=0)
    assert stats['approaches'][4]['rejection_rate'] == 100
    assert stats['weekly'] == [dict(week_start='2026-10-05', count=1),
                              dict(week_start='2026-10-12', count=1),
                              dict(week_start='2026-10-19', count=0),
                              dict(week_start='2026-10-26', count=1)]


def test_endpoint_empty(tmp_path):
    stats = fetch_stats(tmp_path, [])
    assert all(row['count'] == row['percent'] == 0 for row in stats['funnel'])
    assert all(row['screen_rate'] == row['rejection_rate'] == 0 for row in stats['approaches'])
    assert stats['weekly'] == []


def test_primary_hs_classification_and_existing_keys():
    from app.application_stats import application_stats
    rows = [record(0, 'Interviewing'), record(1, 'Offer'), record(2, 'Rejected'),
            record(3), record(4, 'Interviewing', resume_revision='tailored'),
            record(5, 'Offer', created='2026-10-20T00:00:00Z'),
            record(6, 'Offer', resume_snapshot={'draft': {}}),
            record(7, 'Offer', tailoring_revision='v1')]
    stats = application_stats(rows)
    assert set(stats) == {'primary', 'funnel', 'approaches', 'weekly', 'withdrawn', 'unknown_statuses'}
    assert stats['primary']['hs_interviews'] == dict(interviews=2, hs_applications=4, rate=50,
        weekly=[dict(week_start='2026-10-19', hs_applications=4, interviews=2)])


def test_primary_posting_precision_boundaries_and_weekly():
    from app.application_stats import application_stats
    rows = [record(i, created='2026-10-25T12:00:00Z') for i in range(8)]
    posted = ['2026-10-25T06:00:00Z', '2026-10-25T05:59:59Z', None, '2026-10-25',
              '2026-10-25T12:00:01Z', '2026-10-25T07:00:00', '2026-10-25T07:00:00Z',
              '2026-10-25T07:00:00Z']
    for row, value in zip(rows, posted):
        row['job_snapshot'] = {'posted_at': value}
    rows[5]['created_at'] = '2026-10-25T12:00:00'
    rows[6]['created_at'] = None
    rows[7]['created_at'] = '2026-10-25'
    rows[1]['applied_date'] = '2026-11-02'
    result = application_stats(rows)['primary']['applied_within_6h']
    assert result == dict(within_6h=2, measurable=3, unknown=5, rate=66.7,
        unknown_reasons=dict(no_posted_time=1, date_only_posted_time=1,
                             applied_before_posted=1, no_precise_applied_time=2),
        weekly=[dict(week_start='2026-10-19', within_6h=2, measurable=2, unknown=5),
                dict(week_start='2026-10-26', within_6h=0, measurable=0, unknown=0),
                dict(week_start='2026-11-02', within_6h=0, measurable=1, unknown=0)])


def test_posted_lookup_earliest_source_and_snapshot_fallback(tmp_path):
    from app.main import application_posted_times
    from app.application_stats import application_stats
    import sqlite3
    path = tmp_path / 'discovery.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE opportunities(id, job_id, payload); CREATE TABLE sightings(opportunity_id, payload);')
        db.execute('INSERT INTO opportunities VALUES (?,?,?)', ('o', 'external:0', json.dumps({'posted_at': '2026-10-25'})))
        for posted in ['2026-10-25T08:00:00Z', '2026-10-25T06:00:00Z', '2026-10-25']:
            db.execute('INSERT INTO sightings VALUES (?,?)', ('o', json.dumps({'posted_at': posted})))
    times = application_posted_times(path)
    assert times == {'external:0': '2026-10-25T06:00:00Z'}
    rows = [record(0, created='2026-10-25T12:00:00Z'),
            record(1, created='2026-10-25T12:00:00Z', job_snapshot={'posted_at': '2026-10-25T10:00:00Z'})]
    assert application_stats(rows, posted_times=times)['primary']['applied_within_6h']['within_6h'] == 2


def test_endpoint_uses_discovery_posted_time(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        row = record(0, created='2026-10-25T12:00:00Z',
                     job_snapshot={'posted_at': '2026-10-24T00:00:00Z'})
        with client.app.state.applications.db() as db:
            db.execute('INSERT INTO applications VALUES (?,?,?)', (row['id'], row['job_id'], json.dumps(row)))
        with client.app.state.discovery.db() as db:
            db.execute('INSERT INTO opportunities(id,job_id,payload,first_seen,last_seen) VALUES (?,?,?,?,?)',
                       ('o', row['job_id'], json.dumps({'posted_at': '2026-10-25T06:00:00Z'}),
                        '2026-10-25T11:00:00Z', '2026-10-25T11:00:00Z'))
        result = client.get('/api/applications/stats').json()
        assert result['primary']['applied_within_6h']['within_6h'] == 1


def test_primary_addition_preserves_existing_output():
    from app.application_stats import application_stats
    rows = [record(0, 'Interviewing'), record(1, 'Rejected'), record(2, 'Withdrawn')]
    baseline = application_stats(rows)
    enriched = application_stats(rows, posted_times={r['job_id']: '2026-10-21T06:00:00Z' for r in rows})
    assert {k:v for k,v in enriched.items() if k != 'primary'} == {k:v for k,v in baseline.items() if k != 'primary'}
    assert baseline['primary']['applied_within_6h']['unknown'] == 3
    assert enriched['primary']['applied_within_6h']['measurable'] == 3


def test_saved_priorities_reuse_known_jobs_and_look_up_only_new_ones():
    from app.application_stats import SavedPriorities
    calls = []
    def lookup(ids):
        calls.append(set(ids))
        return {j: 'ATTACK_HARD' for j in ids}
    cache = SavedPriorities(lookup)
    assert cache.get({'a', 'b', 'external:x'}) == {'a': 'ATTACK_HARD', 'b': 'ATTACK_HARD'}
    assert cache.get({'a', 'b'}) == {'a': 'ATTACK_HARD', 'b': 'ATTACK_HARD'}
    cache.get({'a', 'b', 'c'})
    assert calls == [{'a', 'b'}, {'c'}]
