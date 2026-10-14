import sqlite3, json
from contextlib import closing
from threading import RLock

from app.tailoring import QUEUE_KEY, Tailoring


class Engine:
    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as db, db: db.execute('CREATE TABLE cache (key TEXT PRIMARY KEY, payload TEXT)')
    def connect(self): return sqlite3.connect(self.path)
    def put(self, key, value):
        with closing(self.connect()) as db, db: db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', (key, json.dumps(value)))
    def get(self, key):
        with closing(self.connect()) as db: row = db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None
    def get_many(self, keys):
        return {k: v for k in keys if (v := self.get(k)) is not None}


def test_list_shows_each_jobs_resume_status(tmp_path):
    t = Tailoring.__new__(Tailoring)
    t.engine, t.lock, t.worker, t.active = Engine(tmp_path/'c.db'), RLock(), None, None
    for job, state in (('a', 'QUEUED'), ('b', 'NEEDS_REVIEW'), ('c', 'RUNNING'), ('d', 'APPROVED')):
        t.engine.put('tailoring:latest:'+job, 'run:'+job); t.engine.put('run:'+job, {'state': state, 'context': {'big': 'x'*1000}})
    t.engine.put(QUEUE_KEY, ['run:a']); t.engine.put('tailoring:export-latest:d', {'revision': 'r'})
    s = t.statuses(['a', 'b', 'c', 'd', 'none'])
    assert s['a'] == {'state': 'QUEUED', 'label': 'Resume queued (#1)'}
    assert s['b']['label'] == 'Resume ready to review'
    assert s['c']['state'] == 'INTERRUPTED'  # Not the active worker, so it stopped.
    assert s['d']['label'] == 'Resume downloaded' and 'none' not in s
