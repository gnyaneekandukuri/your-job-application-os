"""Disposable SQLite read model. Requests never perform assessment or provider work."""
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from threading import Event, Lock, Thread

VERSION = 'job-list-v7-exact-dedup'
log = logging.getLogger(__name__)


class JobList:
    def __init__(self, discovery):
        self.discovery = discovery
        self.path = discovery.path.parent / 'job-list.sqlite3'
        self.stop = Event()
        self.lock = Lock()
        self.worker = None
        self.error = None
        with closing(self.connect()) as db, db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1),
                    fingerprint TEXT, revision TEXT, updated TEXT, metadata TEXT);
                CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY, payload TEXT,
                    search_text TEXT, priority TEXT, location TEXT, company_group TEXT,
                    sort_order INTEGER, backing INTEGER, first_seen TEXT, freshness TEXT,
                    description_length INTEGER, completed INTEGER);
                CREATE INDEX IF NOT EXISTS listing_order ON listings(sort_order,backing DESC,first_seen DESC,id);
                CREATE INDEX IF NOT EXISTS listing_filters ON listings(completed,location,company_group,priority);
                CREATE TABLE IF NOT EXISTS boards (id TEXT, board TEXT, PRIMARY KEY(id,board));
                CREATE INDEX IF NOT EXISTS listing_board ON boards(board,id);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def fingerprint(self):
        # Includes external edits and background worker writes, not just HTTP mutations.
        # Minute bucket expires time-sensitive <24h filters even without a new scan.
        parent = self.discovery.path.parent
        files = []
        for name in ('discovery.sqlite3', 'reviews.sqlite3', 'retrieval.sqlite3',
                     'pursuit-workflow.sqlite3', 'evidence-workflow.sqlite3', 'applications.sqlite3'):
            files.extend([parent / name, parent / (name + '-wal')])
        files.append(parent / 'yc-browser/status.json')
        root = self.discovery.analysis.library.root
        files.extend(p for p in root.rglob('*') if p.is_file())
        values = []
        for path in sorted(files):
            try:
                stat = path.stat()
                values.append((str(path), stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                values.append((str(path), None))
        values.extend([VERSION, int(time.time() // 60), sorted(self.discovery.queued_ids),
                       bool(os.environ.get('OPENAI_API_KEY')), bool(os.environ.get('TYPESAFE_API_KEY'))])
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def refresh(self):
        if not self.lock.acquire(blocking=False):
            return False
        try:
            fingerprint = self.fingerprint()
            with closing(self.connect()) as db:
                saved = db.execute('SELECT fingerprint FROM snapshot WHERE id=1').fetchone()
            if saved and saved[0] == fingerprint:
                self.error = None
                return False
            result = self.discovery.table_view(include_assessments=True)
            from .job_identity import group_listings
            items = group_listings(result.pop('items'))
            from .scan_overview import overview
            result['scan_overview'] = overview(result,items)
            result.get('assessment_results',{}).pop('scans',None)
            board_counts = {}
            listings, boards = [], []
            for item in items:
                item['description_length'] = len(item.pop('description', ''))
                item.pop('description_provenance', None)
                # Original evidence and complete JD are fetched on demand.
                fit = item.get('company_fit') or {}
                fit.pop('evidence', None)
                priority = item.get('keyword_match') or {}
                priority_key = 'not_screened' if priority.get('label') == 'Not screened' else priority.get('priority', 'not_screened')
                listings.append((item['id'], json.dumps(item),
                    ' '.join(item.get(k, '') or '' for k in ('title','company','location')).lower(),
                    priority_key, (item.get('location_match') or {}).get('status','unclear'),
                    fit.get('group','unknown'), fit.get('sort_order',100), fit.get('backing_boost',0),
                    item['first_seen'], item.get('freshness','unknown'), item['description_length'],
                    int((item.get('assessment') or {}).get('state') == 'COMPLETE')))
                for board in {s['board'] for s in item['sources']}:
                    boards.append((item['id'],board))
                    board_counts[board] = board_counts.get(board,0) + 1
            result['board_counts'] = board_counts
            result['saved_count'] = len(items)
            updated = datetime.now(timezone.utc).isoformat()
            revision = hashlib.sha256((fingerprint + updated).encode()).hexdigest()
            # Atomic replacement: a failed refresh never empties the last good list.
            with closing(self.connect()) as db, db:
                db.execute('DELETE FROM listings')
                db.execute('DELETE FROM boards')
                db.executemany('INSERT INTO listings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', listings)
                db.executemany('INSERT INTO boards VALUES(?,?)', boards)
                db.execute('INSERT OR REPLACE INTO snapshot VALUES(1,?,?,?,?)',
                           (fingerprint,revision,updated,json.dumps(result)))
            self.error = None
            return True
        except Exception:
            self.error = 'Could not update the job list. Last saved results are retained; retrying.'
            log.exception('Job-list refresh failed')
            return False
        finally:
            self.lock.release()

    def status(self):
        with closing(self.connect()) as db:
            row = db.execute('SELECT fingerprint,revision,updated FROM snapshot WHERE id=1').fetchone()
        stale = not row or row['fingerprint'] != self.fingerprint()
        return {'ready':bool(row), 'revision':row['revision'] if row else None,
                'updated_at':row['updated'] if row else None, 'refreshing':stale,
                'error':self.error or getattr(self.discovery, 'automatic_error', None),
                'running':bool(self.discovery.worker and self.discovery.worker.is_alive()),
                'automatic_running':bool(self.discovery.worker and self.discovery.worker.is_alive() and self.discovery.worker.name == 'automatic-assessments')}

    def applied_keys(self):
        path = self.path.parent / 'applications.sqlite3'
        if not path.exists(): return set(), set()
        with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro', uri=True, timeout=5)) as db:
            rows = db.execute('SELECT job_id,payload FROM applications').fetchall()
        ids = {r[0] for r in rows}
        records = [json.loads(r[1]) for r in rows]
        urls = {r.get('url','').rstrip('/') for r in records if r.get('url')}
        if not self.discovery.path.exists(): return ids, urls
        from .job_identity import identity
        keys = {identity({**r.get('job_snapshot',{}), 'location':r.get('location','')}) for r in records} - {None}
        with closing(sqlite3.connect('file:'+self.discovery.path.as_posix()+'?mode=ro',uri=True,timeout=5)) as db:
            opportunities = [(oid, jid, json.loads(payload)) for oid,jid,payload in db.execute('SELECT id,job_id,payload FROM opportunities')]
        # Recover identity from the saved listing when older application snapshots lack location.
        keys.update(identity(p) for oid,jid,p in opportunities if jid in ids or 'opportunity:'+oid in ids or p.get('url','').rstrip('/') in urls)
        keys.discard(None)
        for oid,jid,p in opportunities:
            if identity(p) in keys:
                ids.add('opportunity:'+oid)
                if jid: ids.add(jid)
                if p.get('url'): urls.add(p['url'].rstrip('/'))
        return ids, urls

    def page(self, page=1, size=25, query='', priority='all', location='match',
             company='focus', board='all', show='all', runs=False):
        with closing(self.connect()) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM snapshot WHERE id=1').fetchone()
            if not row:
                return {'ready':False,'items':[],'total':0,'page':1,'pages':1,'revision':None}
            metadata = json.loads(row['metadata'])
            metadata.pop('assessment_results',None)
            focus = (metadata.get('company_preference') or {}).get('focus','any')
            where, args = ['completed=0'], []
            applied_ids, applied_urls = self.applied_keys()
            for field, values in (('job_id',applied_ids),('url',applied_urls),('id',{v.removeprefix('opportunity:') for v in applied_ids if v.startswith('opportunity:')})):
                if values:
                    where.append("COALESCE(rtrim(json_extract(payload,'$."+field+"'),'/'),'') NOT IN ("+','.join('?' for _ in values)+')')
                    args.extend(sorted(values))
            include_unknown = company == 'focus' and focus == 'startups'
            if company == 'focus': company = 'startups' if focus == 'startups' else 'established' if focus == 'established' else 'all'
            if company == 'startups': where.append("company_group IN ('preferred','other_startup','unknown')" if include_unknown else "company_group IN ('preferred','other_startup')")
            elif company != 'all': where.append('company_group=?'); args.append(company)
            for column, value in (('location',location),('priority',priority)):
                if value != 'all': where.append(column+'=?'); args.append(value)
            if board != 'all':
                where.append('id IN (SELECT id FROM boards WHERE board=?)'); args.append(board)
            if query: where.append('instr(search_text,?)>0'); args.append(query.lower())
            freshness_counts = {'recent': 0, 'older': 0, 'uncertain': 0}
            for age, count in db.execute('SELECT freshness,count(*) FROM listings WHERE '+
                                        ' AND '.join(where)+' GROUP BY freshness', args):
                bucket = 'recent' if age == 'within_24h' else 'older' if age == 'older' else 'uncertain'
                freshness_counts[bucket] += count
            if show == 'active':
                where.append("(COALESCE(json_extract(payload,'$.age_state'),'unknown') != 'older_than_four_days' OR json_extract(payload,'$.retention_protected')=1)")
            elif show == 'kept': where.append("json_extract(payload,'$.retention_protected')=1")
            elif show == 'watched': where.append("json_extract(payload,'$.company_watched')=1")
            elif show == 'recent': where.append("freshness='within_24h'")
            elif show == 'older': where.append("freshness='older'")
            elif show == 'uncertain': where.append("freshness IN ('date_only','unknown')")
            elif show == 'missing': where.append('description_length<200')
            elif show == 'discovered':
                where.append('first_seen>?'); args.append((datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
            clause = ' AND '.join(where)
            total = db.execute('SELECT count(*) FROM listings WHERE '+clause,args).fetchone()[0]
            pages = max(1,math.ceil(total/size)); page = min(max(1,page),pages)
            order = ('sort_order,backing DESC,' if focus == 'startups' else 'backing DESC,' if focus != 'any' else '')+'first_seen DESC,id'
            items = [] if runs else [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM listings WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',
                [*args,size,(page-1)*size])]
            if not runs:
                metadata.pop('run_reports',None)
                if metadata.get('scan'):
                    metadata['scan']['payload'] = {k:v for k,v in metadata['scan']['payload'].items()
                        if k in ('state','phase','new_count','error')}
            return {**metadata,'ready':True,'items':items,'total':total,'page':page,'pages':pages,
                    'freshness_counts':freshness_counts,
                    'revision':row['revision'],'updated_at':row['updated']}

    def assessments(self):
        with closing(self.connect()) as db:
            row = db.execute('SELECT metadata,revision FROM snapshot WHERE id=1').fetchone()
        if not row: return {'ready':False,'items':[]}
        result = json.loads(row['metadata']).get('assessment_results',{})
        result.pop('scans',None)
        ids, urls = self.applied_keys()
        result['items'] = [r for r in result.get('items',[]) if r.get('job_id') not in ids and (r.get('url') or '').rstrip('/') not in urls]
        if 'counts' in result:
            result['counts']['complete'] = sum(r.get('assessment',{}).get('state')=='COMPLETE' for r in result['items'])
        return {**result,'ready':True,'revision':row['revision']}

    def detail(self, oid):
        from .store import Missing
        with self.discovery.db() as db:
            row = db.execute('SELECT payload FROM opportunities WHERE id=?',(oid,)).fetchone()
        if not row: raise Missing('Opportunity not found')
        item = json.loads(row[0])
        from .company_preferences import company_fit, preferences as company_preferences
        from .discovery_preferences import load_preferences
        root = self.discovery.analysis.library.root
        prefs = load_preferences(root / 'knowledge/discovery-preferences.json')
        return {'company_fit':company_fit(item,company_preferences(root,prefs)),
                **{k:item.get(k,'') for k in ('title','company','description','url')}} | {
            'id':oid, 'content_hash':self.discovery.content_hash(item)}

    def start(self):
        def loop():
            while not self.stop.is_set():
                self.refresh()
                self.stop.wait(2)
        self.worker = Thread(target=loop,daemon=True,name='job-list-projection')
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker: self.worker.join(timeout=10)
