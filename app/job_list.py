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

from .job_identity import listing_urls

VERSION = 'job-list-v15-listing-issues-drain'
log = logging.getLogger(__name__)


class JobList:
    def __init__(self, discovery):
        self.discovery = discovery
        self.path = discovery.path.parent / 'job-list.sqlite3'
        self.stop = Event()
        self.lock = Lock()
        self.applied_lock = Lock()
        self.applied_cache = None
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
        self.install_identity_revisions()

    def install_identity_revisions(self):
        """Only identity changes invalidate exclusions, not scan progress or costs."""
        self.identity_revision_paths = set()
        for path, table in ((self.path.parent/'applications.sqlite3','applications'),
                            (self.discovery.path,'opportunities')):
            if not path.exists(): continue
            with closing(sqlite3.connect(path, timeout=15)) as db, db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone(): continue
                db.execute('CREATE TABLE IF NOT EXISTS job_list_identity_revision(id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
                db.execute('INSERT OR IGNORE INTO job_list_identity_revision VALUES(1,0)')
                condition = ''
                if table == 'opportunities':
                    fields = ('company','title','description','url','source_url','sources','listing_urls')
                    condition = ' WHEN OLD.job_id IS NOT NEW.job_id OR ' + ' OR '.join(
                        "json_extract(OLD.payload,'$."+f+"') IS NOT json_extract(NEW.payload,'$."+f+"')" for f in fields)
                for operation in ('INSERT','UPDATE','DELETE'):
                    db.execute('CREATE TRIGGER IF NOT EXISTS job_list_identity_'+table+'_'+operation+
                               ' AFTER '+operation+' ON '+table+(condition if operation=='UPDATE' else '')+
                               ' BEGIN UPDATE job_list_identity_revision SET revision=revision+1 WHERE id=1; END')
                if table == 'opportunities' and db.execute("SELECT 1 FROM sqlite_master WHERE name='opportunity_aliases'").fetchone():
                    for operation in ('INSERT','UPDATE','DELETE'):
                        db.execute('CREATE TRIGGER IF NOT EXISTS job_list_identity_alias_'+operation+
                                   ' AFTER '+operation+' ON opportunity_aliases BEGIN UPDATE job_list_identity_revision SET revision=revision+1 WHERE id=1; END')
                self.identity_revision_paths.add(path)

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
        files.append(parent / 'lenny-browser/status.json')
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
                    int((item.get('assessment') or {}).get('state') == 'COMPLETE' or bool(item.get('passed')))))
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

    def overview(self):
        with closing(self.connect()) as db:
            row=db.execute('SELECT metadata FROM snapshot WHERE id=1').fetchone()
        if row:
            result=json.loads(row['metadata'])
            result.pop('run_reports', None)
            result.pop('assessment_results', None)
            result.update(self.status(), settings=self.discovery.settings(), items=[])
            return result
        from .discovery_preferences import load_preferences
        preferences=load_preferences(self.discovery.analysis.library.root / 'knowledge/discovery-preferences.json')
        return {**self.status(), 'items':[], 'settings':self.discovery.settings(),
                'location_preference':preferences.get('location_filter',{}),
                'pipeline':{'complete':0,'running':0,'attention':0,'previous':0,'queued':0}}

    def status(self):
        with closing(self.connect()) as db:
            row = db.execute('SELECT fingerprint,revision,updated FROM snapshot WHERE id=1').fetchone()
        stale = not row or row['fingerprint'] != self.fingerprint()
        return {'ready':bool(row), 'revision':row['revision'] if row else None,
                'updated_at':row['updated'] if row else None, 'refreshing':stale,
                'error':self.error or getattr(self.discovery, 'automatic_error', None),
                'running':bool(self.discovery.worker and self.discovery.worker.is_alive()),
                'automatic_running':bool(getattr(self.discovery, 'assessment_worker', None) and self.discovery.assessment_worker.is_alive())}

    def invalidate(self):
        """Let the normal background loop refresh after a settings mutation."""
        with closing(self.connect()) as db, db:
            db.execute('UPDATE snapshot SET fingerprint=NULL WHERE id=1')

    def applied_signature(self):
        values = []
        for base in (self.path.parent / 'applications.sqlite3', self.discovery.path):
            if base in self.identity_revision_paths:
                with closing(sqlite3.connect(base, timeout=5)) as db:
                    values.append(db.execute('SELECT revision FROM job_list_identity_revision WHERE id=1').fetchone()[0])
                continue
            for path in (base, base.with_name(base.name+'-wal'), base.with_name(base.name+'-journal')):
                try:
                    stat = path.stat()
                    values.append((stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
                except FileNotFoundError:
                    values.append(None)
        return tuple(values)

    def applied_keys(self):
        # Single-flight reads: concurrent tabs share one computation. A change to
        # either source (including WAL writes) invalidates the result immediately.
        if not hasattr(self, 'applied_lock'):
            return self.compute_applied_keys()
        with self.applied_lock:
            stamp = self.applied_signature()
            if self.applied_cache and self.applied_cache[0] == stamp:
                return self.applied_cache[1]
            result = self.compute_applied_keys()
            if self.applied_signature() == stamp:
                self.applied_cache = (stamp, result)
            return result

    def compute_applied_keys(self):
        path = self.path.parent / 'applications.sqlite3'
        if not path.exists(): return set(), set()
        with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro', uri=True, timeout=5)) as db:
            rows = db.execute('SELECT job_id,payload FROM applications').fetchall()
        ids = {r[0] for r in rows}
        records = [{**json.loads(payload), 'job_id': jid} for jid, payload in rows]
        from .job_identity import AppliedMatchIndex, ats_keys, company_title_key
        applied = AppliedMatchIndex(records)
        urls = {r.get('url','').rstrip('/') for r in records if r.get('url')}
        if not self.discovery.path.exists(): return ids, urls
        # Applying to one location of an identical posting covers its other locations.
        from .job_identity import same_job as identity
        keys = {identity({**r.get('job_snapshot',{}), 'location':r.get('location','')}) for r in records} - {None}
        with closing(sqlite3.connect('file:'+self.discovery.path.as_posix()+'?mode=ro',uri=True,timeout=5)) as db:
            opportunities = [(oid, jid, json.loads(payload)) for oid,jid,payload in db.execute('SELECT id,job_id,payload FROM opportunities')]
            aliases = db.execute('SELECT old_id,canonical_id,job_id,url FROM opportunity_aliases').fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='opportunity_aliases'").fetchone() else []
        for old,target,jid,url in aliases:
            if jid in ids or 'opportunity:'+old in ids or (url or '').rstrip('/') in urls:
                ids.add('opportunity:'+target)
        for oid,jid,p in opportunities:
            if 'opportunity:'+oid in ids:
                if jid: ids.add(jid)
                if p.get('url'): urls.add(p['url'].rstrip('/'))
        links = set().union(*(listing_urls(r) | listing_urls(r.get('job_snapshot', {})) for r in records))
        for oid, jid, p in opportunities:
            if jid in ids or 'opportunity:'+oid in ids:
                links.update(listing_urls(p))
        for oid, jid, p in opportunities:
            if listing_urls(p) & links:
                ids.add('opportunity:'+oid)
                if jid: ids.add(jid)
                if p.get('url'): urls.add(p['url'].rstrip('/'))
        # Recover identity from the saved listing when older application snapshots lack location.
        keys.update(identity(p) for oid,jid,p in opportunities if jid in ids or 'opportunity:'+oid in ids or p.get('url','').rstrip('/') in urls)
        keys.discard(None)
        # Only matching company/title pairs can share an exact-content identity.
        # Avoid normalizing thousands of unrelated full descriptions on reads.
        import re, unicodedata
        def pair(p):
            return tuple(re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(p.get(k) or ''))).strip().casefold()
                         for k in ('company','title'))
        pairs = {pair(r.get('job_snapshot', {})) for r in records}
        pairs.update(pair(p) for oid,jid,p in opportunities
                     if jid in ids or 'opportunity:'+oid in ids or p.get('url','').rstrip('/') in urls)
        for oid,jid,p in opportunities:
            if pair(p) in pairs and identity(p) in keys:
                # Exact content alone must not defeat explicit requisition IDs.
                candidates = applied.pairs.get(company_title_key(p), ())
                if ats_keys(p) and candidates and not applied.reason(p):
                    continue
                ids.add('opportunity:'+oid)
                if jid: ids.add(jid)
                if p.get('url'): urls.add(p['url'].rstrip('/'))
        # Resolve broader external identities inside the existing stamp cache.
        # Do not seed this index from matched cards: that could transitively hide
        # a different requisition via a duplicate without an ATS ID.
        for oid, jid, p in opportunities:
            if applied.reason(p):
                ids.add('opportunity:' + oid)
                if jid: ids.add(jid)
        return ids, urls

    def page(self, page=1, size=25, query='', priority='all', location='match',
             company='focus', board='all', show='all', runs=False):
        if runs:
            return self.runs(page)
        with closing(self.connect()) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM snapshot WHERE id=1').fetchone()
            if not row:
                return {'ready':False,'items':[],'total':0,'page':1,'pages':1,'revision':None}
            metadata = json.loads(row['metadata'])
            metadata.pop('assessment_results',None)
            focus = (metadata.get('company_preference') or {}).get('focus','any')
            where, args = (["json_extract(payload,'$.passed')=1"] if show == 'passed' else ['completed=0']), []
            applied_ids, applied_urls = self.applied_keys()
            for field, values in (('job_id',applied_ids),('url',applied_urls),('id',{v.removeprefix('opportunity:') for v in applied_ids if v.startswith('opportunity:')})):
                if values:
                    where.append("COALESCE(rtrim(json_extract(payload,'$."+field+"'),'/'),'') NOT IN ("+','.join('?' for _ in values)+')')
                    args.extend(sorted(values))
            # Only listings worth acting on: still active (or saved/kept), not Low priority, not outside preferred locations.
            help_where = where + ["json_array_length(COALESCE(json_extract(payload,'$.needs_help'),'[]'))>0",
                                  "priority!='low'", "location!='outside'",
                                  "(COALESCE(json_extract(payload,'$.age_state'),'unknown') != 'older_than_four_days' OR json_extract(payload,'$.retention_protected')=1)"]
            help_count = db.execute('SELECT count(*) FROM listings WHERE '+' AND '.join(help_where), args).fetchone()[0]
            if show == 'help': where, args = help_where, list(args)
            include_unknown = company == 'focus' and focus == 'startups'
            if show == 'help': company, location, priority, board = 'all', 'all', 'all', 'all'
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
            # High priority first and Low last, then newest discovery day; company preference only breaks ties within a day.
            order = ("CASE priority WHEN 'high' THEN 0 WHEN 'low' THEN 2 ELSE 1 END,substr(first_seen,1,10) DESC,"
                     + ('sort_order,backing DESC,' if focus == 'startups' else 'backing DESC,' if focus != 'any' else '')+'first_seen DESC,id')
            items = [] if runs else [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM listings WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',
                [*args,size,(page-1)*size])]
            if not runs:
                metadata.pop('run_reports',None)
                if metadata.get('scan'):
                    metadata['scan']['payload'] = {k:v for k,v in metadata['scan']['payload'].items()
                        if k in ('state','phase','new_count','error')}
            return {**metadata,'ready':True,'items':items,'total':total,'page':page,'pages':pages,
                    'freshness_counts':freshness_counts,'help_count':help_count,
                    'revision':row['revision'],'updated_at':row['updated']}

    def runs(self, page=1, limit=10):
        # Read only bounded scan payloads; never load discovery snapshot metadata.
        with self.discovery.db() as db:
            total = db.execute('SELECT count(*) FROM scans').fetchone()[0]
            rows = db.execute('SELECT * FROM scans ORDER BY id DESC LIMIT ? OFFSET ?',
                              (limit, (page-1)*limit)).fetchall()
            reports = [self._run(db, row, False) for row in rows]
        # Preserve the small controls/connection metadata without decoding embedded reports.
        keys = ('boards', 'unavailable', 'timezone', 'schedule', 'jev', 'yc_connection',
                'lenny_connection', 'board_counts', 'keyword_profile.target', 'location_preference', 'company_preference')
        with closing(self.connect()) as db:
            row = db.execute('SELECT '+','.join("json_extract(metadata, '$."+k+"')" for k in keys)+
                             ' FROM snapshot WHERE id=1').fetchone()
        metadata = {k: json.loads(v) if isinstance(v, str) and v[:1] in ('{', '[') else v
                    for k, v in zip(keys, row) if v is not None} if row else {}
        metadata.setdefault('schedule', ['08:00', '13:00', '20:00'])
        metadata.setdefault('timezone', str(datetime.now().astimezone().tzinfo))
        from .discovery_sources import BOARDS, UNAVAILABLE
        metadata.setdefault('boards', BOARDS)
        metadata.setdefault('unavailable', UNAVAILABLE)
        metadata.setdefault('board_counts', {})
        target = metadata.pop('keyword_profile.target', None)
        if target is not None:
            metadata['keyword_profile'] = {'target': target, 'experience': []}
        with self.discovery.db() as db:
            latest_row = db.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
            latest = self._run(db, latest_row, False) if latest_row else None
        raw_payload = json.loads(latest_row['payload']) if latest_row else {}
        metadata['scan'] = {**latest, 'payload': {k:latest.get(k) for k in
                            ('state', 'phase', 'new_count', 'error', 'classification', 'sources')}} if latest else None
        if latest:
            metadata['scan']['payload'].update({k: raw_payload.get(k) for k in
                                                ('descriptions', 'description_progress')})
        return {**metadata, 'ready': True, 'items': [], 'run_reports': reports, 'total': total,
                'page': page, 'pages': max(1, math.ceil(total/limit)),
                'settings': self.discovery.settings(),
                'running': bool(self.discovery.worker and self.discovery.worker.is_alive())}

    def _run(self, db, row, full):
        from .discovery import scan_summary
        payload = scan_summary(json.loads(row['payload']))
        result = {k: row[k] for k in ('id', 'started', 'finished')}
        result.update({k: payload.get(k) for k in ('state', 'phase', 'new_count', 'coverage', 'error')})
        result.update(tracked=payload.get('tracking_version') == 1,
                      board_tracking=payload.get('board_tracking', False))
        result['sources'] = {board: {k: v for k, v in source.items()
            if full or k in ('state', 'count', 'coverage', 'checked_at', 'coverage_status', 'listing_issues', 'error')}
            for board, source in payload.get('sources', {}).items()}
        result['classification'] = payload.get('classification')
        fields = {
            'tracked': '1', 'new': "json_extract(payload,'$.newly_saved')=1",
            'repeat': "json_extract(payload,'$.newly_saved')=0",
            'high': "json_extract(payload,'$.priority')='High priority'",
            'worth_checking': "json_extract(payload,'$.priority')='Worth checking'",
            'requested': "json_extract(payload,'$.assessment_requested')=1",
            'completed': "json_extract(payload,'$.assessment_requested')=1 AND json_extract(payload,'$.assessment.state')='COMPLETE'"}
        counts = db.execute('SELECT '+','.join('COALESCE(SUM('+expr+'),0)' for expr in fields.values())+
                            ' FROM scan_roles WHERE scan_id=?', (row['id'],)).fetchone()
        result['counts'] = dict(zip(fields, counts))
        if full:
            result['roles'] = [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM scan_roles WHERE scan_id=?', (row['id'],))]
        return result

    def run(self, scan_id):
        from .store import Missing
        with self.discovery.db() as db:
            row = db.execute('SELECT * FROM scans WHERE id=?', (scan_id,)).fetchone()
            if not row:
                raise Missing('Scan not found')
            return self._run(db, row, True)

    def assessments(self):
        with closing(self.connect()) as db:
            row = db.execute('SELECT metadata,revision FROM snapshot WHERE id=1').fetchone()
        result = json.loads(row['metadata']).get('assessment_results',{}) if row else {'items':[], 'settings':{}, 'queued_count':0}
        queue=getattr(self.discovery.analysis,'manual_queue',None)
        if queue:
            live={r['job_id']:r for r in queue.display_items()}
            existing = {r['job_id']: r for r in result.get('items', [])}
            for jid, item in live.items():
                previous = existing.get(jid, {})
                item.update({k:previous[k] for k in ('entered_at', 'kept') if k in previous})
                if 'entered_at' not in item and hasattr(self.discovery, 'saved_job'):
                    item['entered_at'] = self.discovery.saved_job(jid).created_at.isoformat()
                    with self.discovery.db() as db:
                        item['kept'] = bool(db.execute('SELECT 1 FROM kept_saved_jobs WHERE job_id=?',(jid,)).fetchone())
                        rows = db.execute('SELECT first_seen FROM opportunities WHERE job_id=?',(jid,)).fetchall()
                        if rows:
                            item['entered_at'] = min([datetime.fromisoformat(item['entered_at']), *[datetime.fromisoformat(r[0]) for r in rows]]).isoformat()

            result['items']=[r for r in result.get('items',[]) if r['job_id'] not in live]+list(live.values())
        result.pop('scans',None)
        from .job_retention import age_state
        result['items'] = [r for r in result.get('items', []) if r.get('kept') or
                           age_state({'first_seen':r.get('entered_at')}) != 'older_than_four_days']

        ids, urls = self.applied_keys()
        result['items'] = [r for r in result.get('items',[]) if r.get('job_id') not in ids and (r.get('url') or '').rstrip('/') not in urls]
        if 'counts' in result:
            result['counts']['complete'] = sum(r.get('assessment',{}).get('state')=='COMPLETE' for r in result['items'])
        return {**result,'ready':bool(row or result.get('items')),'revision':row['revision'] if row else ''}

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
