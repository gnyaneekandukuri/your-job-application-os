import json
import sqlite3
from app.job_dedup import consolidate, resolve


def database():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE opportunities(id TEXT PRIMARY KEY,payload TEXT,first_seen TEXT,last_seen TEXT,job_id TEXT,imported_hash TEXT,assessment_state TEXT);
    CREATE TABLE sightings(board TEXT,source_id TEXT,opportunity_id TEXT,payload TEXT,seen_at TEXT,PRIMARY KEY(board,source_id));
    CREATE TABLE revisions(opportunity_id TEXT,content_hash TEXT,payload TEXT,seen_at TEXT,PRIMARY KEY(opportunity_id,content_hash));
    CREATE TABLE selected_assessments(opportunity_id TEXT PRIMARY KEY,state TEXT);
    CREATE TABLE scan_roles(scan_id INTEGER,opportunity_id TEXT,payload TEXT,PRIMARY KEY(scan_id,opportunity_id));
    CREATE TABLE kept_jobs(opportunity_id TEXT PRIMARY KEY);''')
    for oid,jid in [('a',None),('b','saved-job')]:
        item={'company':'Example','title':'PM','location':'NYC','description':'Own product discovery and engineering delivery. '*10,'url':'https://example.com/'+oid}
        db.execute('INSERT INTO opportunities VALUES(?,?,?,?,?,?,?)',(oid,json.dumps(item),'2026-01-01','2026-02-01',jid,'hash',None))
        db.execute('INSERT INTO sightings VALUES(?,?,?,?,?)',('yc',oid,oid,json.dumps(item),'today'))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?)',(oid,'hash',json.dumps(item),'today'))
        db.execute('INSERT INTO scan_roles VALUES(?,?,?)',(1,oid,json.dumps({'stage':'done','id':oid})))
    db.execute("INSERT INTO kept_jobs VALUES('a')")
    return db


def test_consolidation_preserves_sources_saved_job_history_and_is_idempotent():
    db=database()
    with db: assert consolidate(db)=={'a':'b'}
    assert db.execute('SELECT count(*) FROM opportunities').fetchone()[0]==1
    assert db.execute('SELECT job_id FROM opportunities').fetchone()[0]=='saved-job'
    assert resolve(db,'a')=='b'
    assert {r[0] for r in db.execute('SELECT opportunity_id FROM sightings')}=={'b'}
    assert all('description' not in json.loads(r[0]) for r in db.execute('SELECT payload FROM sightings'))
    assert db.execute('SELECT count(*) FROM revisions').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM duplicate_history').fetchone()[0]==1
    assert db.execute('SELECT opportunity_id FROM kept_jobs').fetchone()[0]=='b'
    with db: assert consolidate(db)=={}


def test_active_selection_deferred_and_different_description_preserved():
    db=database();db.execute("INSERT INTO selected_assessments VALUES('a','RUNNING')")
    assert consolidate(db)=={}
    db.execute('DELETE FROM selected_assessments')
    r=json.loads(db.execute("SELECT payload FROM opportunities WHERE id='a'").fetchone()[0]);r['description']+=' Manage a separate team.'
    db.execute("UPDATE opportunities SET payload=? WHERE id='a'",(json.dumps(r),))
    assert consolidate(db)=={}


def test_transaction_rolls_back_deletion_and_reference_changes():
    db=database();db.commit()
    db.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON opportunities BEGIN SELECT RAISE(ABORT,'test'); END")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        with db: consolidate(db)
    assert db.execute('SELECT count(*) FROM opportunities').fetchone()[0]==2
    assert {r[0] for r in db.execute('SELECT opportunity_id FROM sightings')}=={'a','b'}

def test_applied_deleted_alias_still_hides_canonical_role(tmp_path):
    from types import SimpleNamespace
    from app.job_list import JobList
    db=database()
    with db: consolidate(db)
    path=tmp_path/'discovery.sqlite3'
    target=sqlite3.connect(path);db.backup(target);target.close()
    with sqlite3.connect(tmp_path/'applications.sqlite3') as app:
        app.execute('CREATE TABLE applications(job_id TEXT,payload TEXT)')
        app.execute('INSERT INTO applications VALUES(?,?)',('opportunity:a',json.dumps({'url':'https://example.com/a'})))
    model=JobList.__new__(JobList);model.path=tmp_path/'job-list.sqlite3';model.discovery=SimpleNamespace(path=path)
    ids,urls=model.applied_keys()
    assert 'opportunity:b' in ids and 'saved-job' in ids
    assert 'https://example.com/b' in urls


def test_same_posting_in_several_cities_shows_once_with_all_locations():
    from app.job_identity import group_listings
    jd = 'Own product telemetry for data teams and partner with engineering on governed metrics. ' * 4
    base = {'company': 'Example', 'title': 'Senior Data PM', 'description': jd, 'first_seen': '2026-10-01', 'last_seen': '2026-10-02', 'sources': []}
    rows = group_listings([{**base, 'id': 'ny', 'location': 'New York, NY', 'location_match': {'status': 'outside'}},
                           {**base, 'id': 'sf', 'location': 'San Francisco, CA', 'location_match': {'status': 'match'}, 'job_id': 'j1'},
                           {**base, 'id': 'other', 'location': 'Austin, TX', 'title': 'Data PM'}])
    assert len(rows) == 2
    grouped = next(r for r in rows if r['title'] == 'Senior Data PM')
    assert grouped['id'] == 'sf' and sorted(grouped['duplicate_ids']) == ['ny', 'sf']
    assert grouped['location'] == 'New York, NY; San Francisco, CA' and grouped['location_match']['status'] == 'match'
