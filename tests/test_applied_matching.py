import json
import sqlite3
from types import SimpleNamespace
from threading import Lock

import pytest
from app.job_identity import AppliedMatchIndex, listing_url
from app.job_list import JobList

UUID = '11111111-2222-3333-4444-555555555555'

def item(url, title='Senior Product Manager - Platform', company='Fable Labs'):
    return dict(url=url, title=title, company=company)

@pytest.mark.parametrize('logged,card', [
    (f'https://jobs.ashbyhq.com/fable/{UUID}/application', f'https://jobs.ashbyhq.com/fable/{UUID}'),
    (f'https://jobs.lever.co/fable/{UUID}/apply', f'https://jobs.lever.co/fable/{UUID}'),
    ('https://careers.fable.test/job?gh_jid=12345', 'https://boards.greenhouse.io/fable/jobs/12345'),
    ('https://careers.fable.test/job/?jobid=abc&b=2&a=1&utm_source=ad', 'https://careers.fable.test/job?a=1&b=2&jobid=abc'),
    ('https://www.fable.test/job/?x=1&i=ad&preference_id=1&gh_src=2&source=ad&ref=ad&lever-source=ad#top', 'https://fable.test/job?x=1'),
    ('https://www.comeet.com/jobs/fable/old-title/D5.F64', 'https://www.comeet.com/jobs/fable/new-title/D5.F64'),
])
def test_url_and_id_match_despite_title_change(logged, card):
    assert AppliedMatchIndex([item(logged)]).reason(item(card, title='Renamed role'))

@pytest.mark.parametrize('host', ['builtin.com', 'linkedin.com', 'careers.fable.test'])
def test_cross_source_and_duplicates(host):
    index = AppliedMatchIndex([item('https://boards.greenhouse.io/fable/jobs/12345')])
    assert index.reason(item(f'https://{host}/jobs/other', title='Sr. Product Manager – Platform', company='Fable Labs, Inc.')) == 'company_title'
    assert index.reason(item('https://builtin.com/jobs/duplicate')) == 'company_title'

@pytest.mark.parametrize('company,title', [('Fiction Street', 'Technical Product Manager'), ('Cloud Bicycle', 'Senior Product Manager, Customer Care AI')])
def test_same_ats_different_requisition(company, title):
    index = AppliedMatchIndex([item('https://boards.greenhouse.io/f/jobs/12345', title, company)])
    assert index.reason(item('https://careers.fable.test/?gh_jid=12345', title, company)) == 'ats_id'
    assert index.reason(item('https://careers.fable.test/?gh_jid=67890', title, company)) is None
    assert index.reason(item(f'https://jobs.ashbyhq.com/f/{UUID}', title, company)) == 'company_title'


def test_normalization():
    assert listing_url('HTTPS://WWW.FABLE.TEST/job/application/?z=2&a=1#frag') == 'https://fable.test/job?a=1&z=2'


def test_compute_and_cache_invalidate(tmp_path):
    discovery = tmp_path / 'discovery.sqlite3'
    card = item('https://builtin.com/jobs/fable')
    with sqlite3.connect(discovery) as db:
        db.execute('CREATE TABLE opportunities(id TEXT, job_id TEXT, payload TEXT)')
        db.execute('INSERT INTO opportunities VALUES(?,?,?)', ('one', None, json.dumps(card)))
    applications = tmp_path / 'applications.sqlite3'
    with sqlite3.connect(applications) as db:
        db.execute('CREATE TABLE applications(job_id TEXT, payload TEXT)')
    reader = object.__new__(JobList)
    reader.path = tmp_path / 'job-list.sqlite3'
    reader.discovery = SimpleNamespace(path=discovery)
    reader.applied_lock, reader.applied_cache = Lock(), None
    reader.identity_revision_paths = set()
    assert 'opportunity:one' not in reader.applied_keys()[0]
    old = reader.applied_cache
    with sqlite3.connect(applications) as db:
        db.execute('INSERT INTO applications VALUES(?,?)', ('external:fiction', json.dumps(item('https://boards.greenhouse.io/f/jobs/12345'))))
    assert 'opportunity:one' in reader.applied_keys()[0]
    assert reader.applied_cache != old
    cached = reader.applied_cache
    reader.applied_keys()
    assert reader.applied_cache is cached


def test_compute_guard_with_identical_descriptions(tmp_path):
    description = 'Fictional responsibilities and requirements. ' * 20
    applied = item('https://boards.greenhouse.io/f/jobs/12345')
    applied['description'] = description
    other = item('https://careers.fable.test/job?gh_jid=67890')
    other['description'] = description
    with sqlite3.connect(tmp_path / 'applications.sqlite3') as db:
        db.execute('CREATE TABLE applications(job_id TEXT, payload TEXT)')
        db.execute('INSERT INTO applications VALUES(?,?)', ('external:fiction', json.dumps(dict(applied, job_snapshot=applied))))
    with sqlite3.connect(tmp_path / 'discovery.sqlite3') as db:
        db.execute('CREATE TABLE opportunities(id TEXT, job_id TEXT, payload TEXT)')
        db.executemany('INSERT INTO opportunities VALUES(?,?,?)', [('one', None, json.dumps(applied)), ('two', None, json.dumps(other))])
    reader = object.__new__(JobList)
    reader.path = tmp_path / 'job-list.sqlite3'
    reader.discovery = SimpleNamespace(path=tmp_path / 'discovery.sqlite3')
    ids, _ = reader.compute_applied_keys()
    assert 'opportunity:one' in ids
    assert 'opportunity:two' not in ids
