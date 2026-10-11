"""Transactional consolidation of exact duplicate discovery records."""
import json
from .job_identity import identity


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS opportunity_aliases (old_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, job_id TEXT, url TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS duplicate_history (source_table TEXT, old_id TEXT, payload TEXT, PRIMARY KEY(source_table,old_id,payload))")


def resolve(db, oid):
    seen = set()
    while oid not in seen:
        seen.add(oid)
        row = db.execute('SELECT canonical_id FROM opportunity_aliases WHERE old_id=?',(oid,)).fetchone()
        if not row: return oid
        oid = row[0]
    raise ValueError('Duplicate alias cycle')


def source_reference(item):
    return {k:v for k,v in item.items() if k not in ('description','description_provenance')}


def consolidate(db):
    initialize(db)
    groups = {}
    for row in db.execute('SELECT * FROM opportunities ORDER BY first_seen,id').fetchall():
        key = identity(json.loads(row['payload']))
        if key: groups.setdefault(key,[]).append(dict(row))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    active = {r[0] for r in db.execute("SELECT opportunity_id FROM selected_assessments WHERE state IN ('QUEUED','RUNNING')")} if 'selected_assessments' in tables else set()
    aliases = {}
    for members in groups.values():
        if len(members)<2 or any(r['id'] in active for r in members): continue
        # Preserve the existing imported role as the primary listing. Other saved jobs
        # remain in their own stores and are linked through aliases, never deleted.
        primary = next((r for r in members if r['job_id']),members[0])
        target = primary['id']
        for row in members:
            old = row['id']
            if old == target: continue
            item = json.loads(row['payload'])
            db.execute('INSERT OR REPLACE INTO opportunity_aliases VALUES(?,?,?,?)',(old,target,row['job_id'],item.get('url','')))
            db.execute('UPDATE opportunity_aliases SET canonical_id=? WHERE canonical_id=?',(target,old))
            # Migrate references, retaining any colliding historical result rather
            # than silently discarding an assessment attempt or scan outcome.
            for table in sorted(tables - {'opportunities','opportunity_aliases','duplicate_history'}):
                columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
                if 'opportunity_id' not in columns: continue
                for child in db.execute(f'SELECT * FROM "{table}" WHERE opportunity_id=?',(old,)).fetchall():
                    values = dict(zip(columns,child)); values['opportunity_id']=target
                    sql=f'INSERT OR IGNORE INTO "{table}" ('+','.join('"'+c+'"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')'
                    # Rows with unrelated primary keys (sightings/drafts/call logs)
                    # can be reparented directly, without duplicate inserts.
                    pk = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")') if r[5]]
                    if 'opportunity_id' not in pk:
                        continue
                    inserted=db.execute(sql,list(values.values())).rowcount
                    if not inserted and table not in ('revisions','kept_jobs','automatic_candidates'):
                        db.execute('INSERT OR IGNORE INTO duplicate_history VALUES(?,?,?)',(table,old,json.dumps(dict(zip(columns,child)))))
                pk = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")') if r[5]]
                if 'opportunity_id' in pk:
                    db.execute(f'DELETE FROM "{table}" WHERE opportunity_id=?',(old,))
                else:
                    db.execute(f'UPDATE "{table}" SET opportunity_id=? WHERE opportunity_id=?',(target,old))
            for cache in ('keyword_jobs','keyword_matches'):
                if cache in tables: db.execute(f'DELETE FROM {cache} WHERE id=?',(old,))
            db.execute('DELETE FROM opportunities WHERE id=?',(old,))
            aliases[old]=target
        db.execute('UPDATE opportunities SET first_seen=?,last_seen=? WHERE id=?',
                   (min(r['first_seen'] for r in members),max(r['last_seen'] for r in members),target))
    # Sightings are provenance, not another full copy of the JD. The opportunity
    # and versioned revisions retain descriptions; sources retain URL/date metadata.
    if 'sightings' in tables:
        for board,sid,payload in db.execute('SELECT board,source_id,payload FROM sightings').fetchall():
            compact=source_reference(json.loads(payload))
            if compact != json.loads(payload): db.execute('UPDATE sightings SET payload=? WHERE board=? AND source_id=?',(json.dumps(compact),board,sid))
    return aliases
