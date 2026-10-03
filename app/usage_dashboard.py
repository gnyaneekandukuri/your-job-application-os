"""Read-only local usage view. Scan attribution is explicit, never inferred by time."""
import json
import sqlite3
from contextlib import closing


def usage_report(directory):
    calls, scans, opportunities = [], [], {}
    with closing(sqlite3.connect(directory/'reviews.sqlite3')) as db:
        jobs = {r[0]: json.loads(r[1]) for r in db.execute('SELECT id,payload FROM jobs')}
    discovery = directory/'discovery.sqlite3'
    if discovery.exists():
        with closing(sqlite3.connect(discovery)) as db:
            for oid, payload, job_id in db.execute('SELECT id,payload,job_id FROM opportunities'):
                opportunities[oid] = {**json.loads(payload), 'job_id':job_id}
            for sid, started, finished, payload in db.execute('SELECT id,started,finished,payload FROM scans ORDER BY id DESC'):
                value = json.loads(payload)
                scans.append({'id':sid, 'started':started, 'finished':finished,
                              'state':value.get('state'), 'boards':value.get('boards',[]),
                              'new_roles':value.get('new_count',0),
                              'tracked':bool(value.get('cost_tracking_version')),
                              'cached_classifications':value.get('classification',{}).get('cached',0),
                              'cached_screenings':value.get('screening',{}).get('cached',0)})
    for path in directory.rglob('*.sqlite3'):
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, provider in [('calls','OpenAI'),('jev_assessment_calls','Jev'),('jev_classification_calls','Jev')]:
                if table not in tables: continue
                for row in db.execute(f'SELECT * FROM {table}'):
                    value = dict(row)
                    value['record_id'] = f'{path.relative_to(directory).as_posix()}:{table}:{value["id"]}'
                    value['provider'] = provider
                    if provider == 'Jev':
                        value.update(cost_basis='estimated_input_rate', reserved=0)
                    if table == 'jev_classification_calls':
                        value['stage'] = 'role_classification'
                    calls.append(value)
            # Legacy successful classifications predate the durable attempts table.
            # Do not count both the cached response and its new ledger entry.
            if 'jev_classifications' in tables:
                tracked = {r[0] for r in db.execute('SELECT fingerprint FROM jev_classification_calls')} if 'jev_classification_calls' in tables else set()
                for fingerprint, payload in db.execute('SELECT fingerprint,payload FROM jev_classifications'):
                    if fingerprint in tracked: continue
                    v = json.loads(payload)
                    calls.append({'id':fingerprint, 'record_id':'legacy-classification:'+fingerprint,
                                  'created_at':v['checked_at'], 'model':v['model'], 'provider':'Jev',
                                  'stage':'role_classification', 'state':'RECEIVED', 'input_tokens':v['input_tokens'],
                                  'output_tokens':None, 'cost':v['estimated_cost_usd'],
                                  'cost_basis':'estimated_input_rate', 'reserved':0})
    for value in calls:
        opportunity = opportunities.get(value.get('opportunity_id'), {})
        job = jobs.get(value.get('job_id'), {})
        value.update(company=job.get('company') or opportunity.get('company'),
                     title=job.get('title') or opportunity.get('title'),
                     job_id=value.get('job_id') or opportunity.get('job_id'))
    return {'calls':sorted(calls,key=lambda c:c['created_at'],reverse=True), 'scans':scans}
