import json
import sqlite3
from contextlib import closing


def usage_report(directory):
    calls = []
    with closing(sqlite3.connect(directory/'reviews.sqlite3')) as db:
        jobs = {r[0]: json.loads(r[1]) for r in db.execute('SELECT id,payload FROM jobs')}
    for path in directory.rglob('*.sqlite3'):
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_assessment_calls'").fetchone():
                for row in db.execute('SELECT * FROM jev_assessment_calls'):
                    value = dict(row)
                    job = jobs.get(value.get('job_id'), {})
                    value.update(provider='Jev', company=job.get('company'), title=job.get('title'),
                                 cost_basis='estimated_input_rate', reserved=0)
                    calls.append(value)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='calls'").fetchone(): continue
            for row in db.execute('SELECT * FROM calls ORDER BY created_at DESC'):
                value = dict(row)
                job = jobs.get(value.get('job_id'), {})
                value.update(company=job.get('company'), title=job.get('title'), provider='OpenAI')
                calls.append(value)
    path = directory/'discovery.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_classifications'").fetchone():
                for row in db.execute('SELECT payload FROM jev_classifications'):
                    v=json.loads(row[0])
                    calls.append({'id':len(calls), 'created_at':v['checked_at'], 'model':v['model'], 'provider':'Jev',
                                  'stage':'role_classification', 'state':'RECEIVED', 'input_tokens':v['input_tokens'],
                                  'output_tokens':None, 'cost':v['estimated_cost_usd'], 'cost_basis':'estimated_input_rate', 'reserved':0})
    return {'calls':sorted(calls,key=lambda c:c['created_at'],reverse=True)}
