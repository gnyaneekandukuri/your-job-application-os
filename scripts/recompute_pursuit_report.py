"""Read saved recommendations and replay Jev answers without writes or dispatch.

Run: .venv\\Scripts\\python.exe -m scripts.recompute_pursuit_report [--data-dir PATH]
All source SQLite connections use mode=ro; a miss raises before any ledger write.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from threading import RLock

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from app.jev_client import JevClient
from app.jev_pursuit import interpret
from app.models import Job
from app.pursuit import recommend
from app.retrieval import digest
from app.user_workspace import default_data_directory


class CacheMiss(Exception):
    pass


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


class ReadOnlyCache:
    def __init__(self, path):
        self.path, self.lock = path, RLock()

    def get(self, key):
        with closing(readonly(self.path)) as db:
            row = db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        raise CacheMiss('No validated cached answer')


def forbidden_dispatch(body):
    raise AssertionError('Dispatch forbidden in cache-only report')


def cached_client(engine):
    # Avoid JevClient.__init__, which creates/migrates the usage ledger.
    client = JevClient.__new__(JevClient)
    client.engine, client.post, client.attempt_id = engine, forbidden_dispatch, 'readonly-report'
    return client


def report(root):
    cache = ReadOnlyCache(root / 'retrieval.sqlite3')
    profile = json.loads((root / 'workspace/knowledge/profile.json').read_text(encoding='utf-8'))
    policy = json.loads((root / 'workspace/criteria/pursuit-policy.json').read_text(encoding='utf-8'))
    with closing(readonly(root / 'reviews.sqlite3')) as db:
        jobs = {j.id:j for (payload,) in db.execute('SELECT payload FROM jobs')
                for j in [Job.model_validate_json(payload)]}
    saved = {}
    serde = JsonPlusSerializer()
    with closing(readonly(root / 'pursuit-workflow.sqlite3')) as db:
        for thread, kind, blob in db.execute('SELECT thread_id,type,checkpoint FROM checkpoints ORDER BY checkpoint_id DESC'):
            result = serde.loads_typed((kind, blob))['channel_values'].get('result', {})
            jid = thread.split(':')[-2]
            if jid not in saved and 'recommendation' in result:
                saved[jid] = result
    rows, misses = [], []
    for jid, old in sorted(saved.items()):
        job = jobs.get(jid)
        if job is None:
            misses.append((jid, 'Saved job unavailable'))
            continue
        try:
            if digest(json.dumps(profile, sort_keys=True)) != old['input_versions']['profile']:
                raise CacheMiss('Saved profile differs from current profile')
            run = cache.get('role-analysis:' + jid + ':' + old['input_versions']['analysis'])
            if not run or digest(json.dumps(run, sort_keys=True)) != old['input_versions']['report']:
                raise CacheMiss('Exact saved evidence run unavailable')
            new = recommend(run, interpret(cached_client(cache), job, run, profile), policy)
            rows.append(dict(job_id=jid, company=job.company, title=job.title, score=new['score'],
                             old=old['recommendation'], new=new['recommendation']))
        except CacheMiss as exc:
            misses.append((jid, str(exc)))
    return rows, misses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=default_data_directory())
    args = parser.parse_args()
    rows, misses = report(args.data_dir)
    print('job_id | company | title | score | old recommendation | new recommendation')
    for row in rows:
        print(' | '.join(str(v) for v in row.values()))
    print('\nCHANGED')
    for row in rows:
        if row['old'] != row['new']:
            print(' | '.join(str(v) for v in row.values()))
    print(f'\nRecomputed: {len(rows)}; cache misses: {len(misses)}; dispatches: 0')
    for jid, reason in misses:
        print(f'CACHE MISS {jid}: {reason}')


if __name__ == '__main__':
    main()
