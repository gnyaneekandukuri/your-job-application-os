"""Local discovery retention. Saved work is never a cleanup candidate."""
import json
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from .store import Missing


class KeepJob(BaseModel):
    keep: bool


class WatchCompany(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    company: str = Field(min_length=1, max_length=200)
    careers_url: HttpUrl | None = None
    reason: str = Field(default='', max_length=2000)
    roles: str = Field(default='', max_length=1000)
    remove: bool = False


def company_key(name):
    return ' '.join(name.casefold().split())


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS kept_jobs (opportunity_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS seen_jobs (id TEXT PRIMARY KEY, url TEXT, first_seen TEXT,
            posted_at TEXT, deleted_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS seen_job_url ON seen_jobs(url);
        CREATE TABLE IF NOT EXISTS seen_sources (board TEXT, source_id TEXT, opportunity_id TEXT,
            PRIMARY KEY(board,source_id));
        CREATE TABLE IF NOT EXISTS company_watchlist (id TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS retention_status (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT);
    ''')


def posting_upper_bound(item):
    """Latest possible publication instant; do not infer publication from discovery."""
    if item.get('date_basis') in ('unknown', 'hiring_comment_timestamp', 'source_updated_at'):
        return None
    try:
        if item.get('date_basis') == 'source_relative_age':
            return datetime.fromisoformat(item['older_after']) - timedelta(days=1)
        value = item.get('posted_at') or ''
        if len(value) == 10:
            # Date-only sources have no timezone: allow the full day anywhere on earth.
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) + timedelta(hours=36)
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else None
    except (ValueError, TypeError, KeyError):
        return None


def age_state(item, current=None):
    current = current or datetime.now(timezone.utc)
    stamp = posting_upper_bound(item)
    if stamp is None or stamp.tzinfo is None or stamp > current:
        return 'unknown'
    return 'older_than_four_days' if current - stamp >= timedelta(days=4) else 'active'


def keep(discovery, oid, value):
    with discovery.lock, discovery.db() as db:
        if not db.execute('SELECT 1 FROM opportunities WHERE id=?', (oid,)).fetchone():
            raise Missing('Opportunity not found')
        if value:
            db.execute('INSERT OR IGNORE INTO kept_jobs VALUES(?)', (oid,))
        else:
            db.execute('DELETE FROM kept_jobs WHERE opportunity_id=?', (oid,))
    return {'kept': value}


def watchlist(discovery):
    with discovery.db() as db:
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM company_watchlist ORDER BY id')]


def status(discovery):
    with discovery.db() as db:
        row = db.execute('SELECT payload FROM retention_status WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {'checked_at':None,'deleted':0,'active_days':4,'delete_after_days':14}


def watch(discovery, data):
    key = company_key(data.company)
    if not key:
        raise ValueError('Company name is required')
    with discovery.lock, discovery.db() as db:
        if data.remove:
            db.execute('DELETE FROM company_watchlist WHERE id=?', (key,))
        else:
            record = data.model_dump(mode='json', exclude={'remove'})
            record['company'] = data.company.strip()
            db.execute('INSERT OR REPLACE INTO company_watchlist VALUES(?,?)', (key, json.dumps(record)))
    return watchlist(discovery)


def cleanup(discovery, current=None):
    current = current or datetime.now(timezone.utc)
    with discovery.lock, discovery.db() as db:
        # Importing a role creates a saved role, including automatic deep assessments.
        # Explicit selections, pins, and every imported role are protected.
        protected = {r[0] for r in db.execute('SELECT opportunity_id FROM kept_jobs')}
        protected.update(r[0] for r in db.execute('SELECT opportunity_id FROM selected_assessments'))
        protected.update(discovery.queued_ids)
        deleted = 0
        db.execute('PRAGMA secure_delete=ON')
        for row in db.execute('SELECT * FROM opportunities WHERE job_id IS NULL').fetchall():
            if row['id'] in protected:
                continue
            item = json.loads(row['payload'])
            stamp = posting_upper_bound(item)
            if stamp is None or stamp.tzinfo is None or current - stamp < timedelta(days=14):
                continue
            oid = row['id']
            db.execute('INSERT OR IGNORE INTO seen_jobs VALUES(?,?,?,?,?)',
                       (oid, item['url'], row['first_seen'], item.get('posted_at'), current.isoformat()))
            db.execute('INSERT OR REPLACE INTO seen_sources SELECT board,source_id,opportunity_id FROM sightings WHERE opportunity_id=?', (oid,))
            for table in ('sightings','revisions','description_fetches','automatic_assessments',
                          'automatic_versions','automatic_candidates','discovery_drafts'):
                db.execute(f'DELETE FROM {table} WHERE opportunity_id=?', (oid,))
            for table in ('keyword_jobs', 'keyword_matches'):
                db.execute(f'DELETE FROM {table} WHERE id=?', (oid,))
            # Keep scan outcome/count history, but remove copied descriptions/evidence.
            for scan in db.execute('SELECT scan_id,payload FROM scan_roles WHERE opportunity_id=?', (oid,)).fetchall():
                data = json.loads(scan['payload'])
                data = {k:v for k,v in data.items() if k in ('id','company','title','stage','priority','newly_saved')}
                data['removed_by_retention'] = True
                db.execute('UPDATE scan_roles SET payload=? WHERE scan_id=? AND opportunity_id=?',
                           (json.dumps(data),scan['scan_id'],oid))
            db.execute('DELETE FROM opportunities WHERE id=?', (oid,))
            deleted += 1
        result = {'checked_at':current.isoformat(), 'deleted':deleted, 'active_days':4, 'delete_after_days':14}
        db.execute('INSERT OR REPLACE INTO retention_status VALUES(1,?)', (json.dumps(result),))
    return result
