"""Persistent local discovery queue and three-times-daily scheduler."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock, Thread, Event
from uuid import uuid4, uuid5, NAMESPACE_URL

from pydantic import BaseModel, ConfigDict, Field
from .discovery_sources import BOARDS, UNAVAILABLE, canonical, collect, freshness, listing_freshness
from .discovery_preferences import load_preferences
from .discovery_network import diagnose
from .yc_connection import YCConnection
from .discovery_location import location_match
from .discovery_keywords import KeywordIndex
from .discovery_jev import JevClassifier
from .models import JobInput
from .role_analysis import AnalyzeInput
from .store import Missing, Conflict


def now(): return datetime.now(timezone.utc).isoformat()


def slot_at(current):
    """Machine-local clock, including its DST offset; one catch-up per latest due slot."""
    due = [h for h in (8, 13, 20) if current.hour >= h]
    return current.strftime('%Y-%m-%d') + f'T{due[-1]:02d}' if due else None


def scan_summary(payload):
    result = dict(payload)
    if result.get('state') == 'complete':
        states = [source['state'] for source in result.get('sources', {}).values()]
        good = sum(state in ('checked', 'partial') for state in states)
        result['state'] = 'failed' if not good else 'incomplete' if good < len(result.get('boards', BOARDS)) or 'partial' in states else 'complete'
    return result


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    auto_assess: bool


class DescriptionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=20, max_length=50000)
    extraction_id: str | None = None


class Discovery:
    def __init__(self, path, service, analysis, collector=collect, sources=lambda: []):
        self.path, self.service, self.analysis, self.collector = path, service, analysis, collector
        self.lock, self.stop = RLock(), Event()
        self.yc = YCConnection(path.parent)
        if collector is collect:
            self.collector = self.collect_board
        self.worker = self.scheduler = None
        self.queued_ids = set()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL, job_id TEXT, imported_hash TEXT, assessment_state TEXT);
                CREATE TABLE IF NOT EXISTS sightings (board TEXT, source_id TEXT, opportunity_id TEXT,
                payload TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY(board,source_id));
                CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, started TEXT, finished TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT);
                CREATE TABLE IF NOT EXISTS import_intents (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automatic_assessments (opportunity_id TEXT PRIMARY KEY, state TEXT, attempted_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS discovery_drafts (id TEXT PRIMARY KEY, opportunity_id TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS description_fetches (opportunity_id TEXT PRIMARY KEY, state TEXT, checked_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS revisions (opportunity_id TEXT, content_hash TEXT, payload TEXT, seen_at TEXT,
                PRIMARY KEY(opportunity_id,content_hash));''')
            db.execute('INSERT OR IGNORE INTO config VALUES (1,?)', (json.dumps({'enabled': False, 'auto_assess': False, 'last_slot': slot_at(datetime.now().astimezone())}),))
            # A terminated process cannot still be scanning in this single-server application.
            db.execute("UPDATE scans SET finished=?, payload=? WHERE finished IS NULL", (now(), json.dumps({'state': 'interrupted', 'sources': {}, 'error': 'Server stopped during this scan; saved listings were retained.'})))

        self.keywords = KeywordIndex(path, sources)
        self.jev = JevClassifier(path)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.db() as db: return json.loads(db.execute('SELECT payload FROM config WHERE id=1').fetchone()[0])

    def configure(self, data):
        with self.lock, self.db() as db:
            cfg = self.settings(); cfg.update(data.model_dump())
            if data.auto_assess: cfg.pop('auto_assess_pause_reason', None)
            db.execute('UPDATE config SET payload=? WHERE id=1', (json.dumps(cfg),))
        return self.view()

    def pause_on_provider_limit(self, error):
        if '(HTTP 429)' not in error: return False
        with self.lock, self.db() as db:
            cfg = self.settings()
            cfg.update(auto_assess=False, auto_assess_pause_reason='OpenAI returned HTTP 429. Automatic analysis is paused; check API billing or rate limits before re-enabling it.')
            db.execute('UPDATE config SET payload=? WHERE id=1',(json.dumps(cfg),))
        return True

    def assessment_results(self):
        """All saved deep analyses, including roles entered without discovery."""
        with self.db() as db:
            discovered = {r['job_id']: dict(r) for r in db.execute('SELECT * FROM opportunities WHERE job_id IS NOT NULL')}
        items = []
        for job_id in self.service.store.ids():
            job = self.service.store.read(job_id)[0]
            row = discovered.get(job_id)
            changed = bool(row and row['imported_hash'] != self.content_hash(json.loads(row['payload'])))
            summary = self.assessment_summary({'job_id':job_id,'description_changed':changed})
            if summary['state'] == 'NOT_STARTED': continue
            items.append({'job_id':job_id,'company':job.company,'title':job.title,'url':str(job.source_url or ''),'assessment':summary})
        return {'items':items, 'queued_count':len(self.queued_ids), 'settings':self.settings()}

    def view(self, include_analysis=True):
        preferences = load_preferences(self.analysis.library.root / "knowledge/discovery-preferences.json")
        with self.db() as db:
            items = []
            for row in db.execute('SELECT * FROM opportunities ORDER BY first_seen DESC'):
                item = json.loads(row['payload'])
                sources = [dict(r) for r in db.execute('SELECT board,seen_at,payload FROM sightings WHERE opportunity_id=?', (row['id'],))]
                item.update(id=row['id'], first_seen=row['first_seen'], last_seen=row['last_seen'], job_id=row['job_id'],
                            assessment_state=row['assessment_state'], freshness=listing_freshness(item),
                            description_changed=bool(row['imported_hash'] and row['imported_hash'] != self.content_hash(item)),
                            sources=[{'board': s['board'], 'seen_at': s['seen_at'], 'posted_at': json.loads(s['payload']).get('posted_at')} for s in sources])
                item["location_match"] = location_match(item.get("location"), preferences)
                us_state = (item.get('search_country') == 'US' and re.search(
                    r',\s*(AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\s*$', item.get('location', '')))
                if ((item['location_match']['status'] == 'unclear' or us_state) and item.get('search_country')
                        and item.get('source_filter') == 'past_24_hours'
                        and any(s['board'] == 'linkedin' for s in sources)):
                    code = item['search_country']
                    target = preferences.get('location_filter', {}).get('countries', [])
                    item['location_match'] = {**item['location_match'], 'status': 'match' if code in target else 'outside',
                                              'countries': [code], 'reason': f'LinkedIn search country: {code}; listing location retained as provided.'}
                item["content_hash"] = self.content_hash(item)
                attempt = db.execute('SELECT state,attempted_at,error FROM automatic_assessments WHERE opportunity_id=?',(row['id'],)).fetchone()
                item['automatic_assessment'] = dict(attempt) if attempt else None
                item['queued_for_assessment'] = row['id'] in self.queued_ids
                fetched = db.execute('SELECT state,checked_at,error FROM description_fetches WHERE opportunity_id=?',(row['id'],)).fetchone()
                item['description_fetch'] = dict(fetched) if fetched else None
                items.append(item)
            scan = db.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
        # Never hold a listing cursor/read lock during filesystem-heavy analysis summaries.
        if include_analysis:
            for item in items:
                item["assessment"] = self.assessment_summary(item)
        keyword_profile = self.keywords.enrich(items, preferences) if include_analysis else None
        if include_analysis:
            self.jev.annotate(items)
        return {'jev': self.jev.status(), 'yc_connection': self.yc.status(), 'keyword_profile': keyword_profile, 'items': items, 'location_preference': preferences.get('location_filter', {}), 'settings': self.settings(), 'running': bool(self.worker and self.worker.is_alive()),
                'scan': {**dict(scan), 'payload': scan_summary(json.loads(scan['payload']))} if scan else None,
                'boards': BOARDS, 'unavailable': UNAVAILABLE, 'timezone': str(datetime.now().astimezone().tzinfo),
                'schedule': ['08:00', '13:00', '20:00']}

    def collect_board(self, board):
        if board == 'yc':
            return self.yc.collect()
        if board == 'linkedin':
            prefs = load_preferences(self.analysis.library.root / 'knowledge/discovery-preferences.json')
            return collect(board, stop=self.stop, countries=prefs.get('location_filter', {}).get('countries', []))
        return collect(board, stop=self.stop)

    def assessment_summary(self, item):
        if not item.get('job_id'):
            return {'state': 'NEEDS_JD' if len(item.get('description','')) < 20 else 'NOT_STARTED', 'score': None, 'recommendation': None}
        if item.get('description_changed'):
            return {'state':'JD_CHANGED', 'score':None, 'recommendation':None, 'stage':'Review the updated JD before assessing it.'}
        from .retrieval import RetrievalError
        from .pursuit import PursuitService
        try:
            # Polling must not reconcile the review graph or compete for its lock.
            job = self.service.store.read(item['job_id'])[0]
            view = self.analysis.view(job, compact=True)
            run = view.get('run')
            if view.get('previous_result_outdated'):
                return {'state':'STALE','score':None,'recommendation':None,'stage':'Context or criteria changed. Reassess when ready.'}
            state = run['state'] if run else 'NOT_STARTED'
            score = view.get('score')
            result = {'state':state,'stage':run.get('stage','') if run else '', 'error':run.get('error','') if run else '',
                      'score':score.get('percent') if score else None, 'provisional':bool(score and score.get('provisional')),
                      'recommendation':None,'choice':None}
            if state == 'COMPLETE':
                pursuit = PursuitService(self.analysis).view(job)
                result['recommendation'] = (pursuit.get('result') or {}).get('recommendation')
                result['choice'] = (pursuit.get('choice') or {}).get('choice')
                automatic = pursuit.get('automatic') or {}
                result['recommendation_state'] = automatic.get('state','PENDING')
                result['recommendation_error'] = automatic.get('error','')
            return result
        except (RetrievalError, Missing, Conflict) as exc:
            return {'state':'NEEDS_ATTENTION','score':None,'recommendation':None,'error':str(exc)}

    def fetch_description(self, oid):
        from .extraction import extract_job
        with self.db() as db:
            row = db.execute('SELECT payload FROM opportunities WHERE id=?',(oid,)).fetchone()
        if not row: raise Missing('Opportunity not found')
        item = json.loads(row[0])
        draft = extract_job(item['url']).model_dump(mode='json')
        draft_id = str(uuid4())
        with self.db() as db:
            db.execute('INSERT INTO discovery_drafts VALUES(?,?,?)',(draft_id,oid,json.dumps(draft)))
        return {**draft, 'warnings':[w for w in draft['warnings'] if not w.startswith('Extraction is not verification')], 'extraction_id':draft_id, 'expected_hash':self.content_hash(item)}

    def save_description(self, oid, data):
        with self.lock, self.db() as db:
            row = db.execute('SELECT * FROM opportunities WHERE id=?',(oid,)).fetchone()
            if not row: raise Missing('Opportunity not found')
            item = json.loads(row['payload'])
            if self.content_hash(item) != data.expected_hash:
                raise Conflict('Listing changed. Reopen the JD before saving.')
            provenance = {'method':'user_paste', 'saved_at':now()}
            if data.extraction_id:
                draft = db.execute('SELECT payload FROM discovery_drafts WHERE id=? AND opportunity_id=?',(data.extraction_id,oid)).fetchone()
                if not draft: raise Conflict('Fetched JD does not belong to this opportunity.')
                provenance = {'method':'user_reviewed_extraction','saved_at':now(),'draft':json.loads(draft[0])}
            item.update(title=data.title,company=data.company,description=data.description,description_provenance=provenance)
            content_hash = self.content_hash(item)
            # Explicitly accepting a changed snapshot starts a new role version;
            # the previous saved role/report remains available in All roles.
            changed = bool(row['imported_hash'] and row['imported_hash'] != content_hash)
            db.execute('UPDATE opportunities SET payload=?,job_id=?,imported_hash=?,assessment_state=? WHERE id=?',
                       (json.dumps(item),None if changed else row['job_id'],None if changed else row['imported_hash'],None if changed else row['assessment_state'],oid))
            db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)',(oid,content_hash,json.dumps(item),now()))
        return self.view()

    @staticmethod
    def content_hash(item):
        return hashlib.sha256(json.dumps({k: item.get(k) for k in ('title', 'company', 'description')}, sort_keys=True).encode()).hexdigest()

    def save(self, board, item):
        item = dict(item); item['url'] = canonical(item['url'])
        oid = hashlib.sha256(item['url'].encode()).hexdigest()
        stamp = now()
        with self.lock, self.db() as db:
            previous = db.execute('SELECT opportunity_id FROM sightings WHERE board=? AND source_id=?', (board, item['source_id'])).fetchone()
            if previous: oid = previous[0]
            row = db.execute('SELECT payload FROM opportunities WHERE id=?', (oid,)).fetchone()
            merged = item
            if row:
                old = json.loads(row[0])
                # A failed detail fetch or metadata-only source must not erase a good description.
                merged = {**old, **{k: v for k, v in item.items() if v not in ('', None, 'Unknown')}}
                if item.get('date_basis') == 'source_relative_age':
                    # A later unparseable age must not inherit an old freshness claim.
                    for key in ('fresh_until', 'older_after', 'age_observed_at'):
                        merged[key] = item.get(key)
            db.execute('INSERT INTO opportunities(id,payload,first_seen,last_seen) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen', (oid, json.dumps(merged), stamp, stamp))
            db.execute('INSERT OR REPLACE INTO sightings VALUES(?,?,?,?,?)', (board, item['source_id'], oid, json.dumps(item), stamp))
            db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)', (oid, self.content_hash(merged), json.dumps(merged), stamp))
        return oid, row is None

    def complete_descriptions(self, seen_ids):
        """Fetch missing JDs without a model; mismatches require manual review."""
        from .extraction import extract_job
        from .safe_fetch import ExtractionError
        def identity(text):
            return re.sub(r'\b(inc|llc|ltd|company|corporation)\b', '', re.sub(r'[^a-z0-9]+',' ',text.lower())).strip()
        counts = {'fetched':0, 'needs_paste':0}
        for item in self.view(include_analysis=False)['items']:
            if self.stop.is_set(): break
            if item['id'] not in seen_ids or len(item.get('description','')) >= 20 or item['location_match']['status'] == 'outside': continue
            previous = item.get('description_fetch')
            if previous and freshness(previous['checked_at']) == 'within_24h':
                counts['needs_paste'] += 1
                continue
            try:
                draft = extract_job(item['url']).model_dump(mode='json')
                if (identity(draft['title']) != identity(item['title']) or identity(draft['company']) != identity(item['company']) or len(draft['description']) < 200):
                    raise ValueError('Listing identity or description needs review')
                with self.lock, self.db() as db:
                    row = db.execute('SELECT payload FROM opportunities WHERE id=?',(item['id'],)).fetchone()
                    current = json.loads(row[0])
                    if self.content_hash(current) != item['content_hash']: continue
                    current.update(description=draft['description'], description_provenance={'method':'listing_extraction','saved_at':now(),'draft':draft})
                    db.execute('UPDATE opportunities SET payload=? WHERE id=?',(json.dumps(current),item['id']))
                    db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)',(item['id'],self.content_hash(current),json.dumps(current),now()))
                    db.execute('INSERT OR REPLACE INTO description_fetches VALUES(?,?,?,?)',(item['id'],'FETCHED',now(),''))
                counts['fetched'] += 1
            except Exception as exc:
                message = str(exc) if isinstance(exc,ExtractionError) else 'Full JD could not be verified against this listing.'
                with self.db() as db:
                    db.execute('INSERT OR REPLACE INTO description_fetches VALUES(?,?,?,?)',(item['id'],'NEEDS_PASTE',now(),message))
                counts['needs_paste'] += 1
        return counts

    def start(self, boards=None):
        with self.lock:
            if self.worker and self.worker.is_alive(): return self.view()
            self.worker = Thread(target=self.scan, args=(boards,), daemon=True)
            self.worker.start()
        return self.view()

    def scan(self, boards=None):
        boards = list(boards) if boards is not None else list(BOARDS)
        if not boards or any(board not in BOARDS for board in boards):
            raise ValueError("Unknown discovery source")
        result = {'state': 'running', 'phase':'collecting', 'boards': boards, 'sources': {}, 'new_count': 0}
        with self.db() as db:
            sid = db.execute('INSERT INTO scans(started,payload) VALUES(?,?)', (now(), json.dumps(result))).lastrowid
        pending = set()
        try:
            for board in boards:
                if self.stop.is_set(): break
                try:
                    rows, coverage, warnings = self.collector(board)
                    if self.stop.is_set(): break
                    for row in rows:
                        oid, new = self.save(board, row)
                        pending.add(oid)
                        if new:
                            result['new_count'] += 1
                    result['sources'][board] = {'state': 'partial' if warnings or 'partial' in coverage.lower() or 'not established' in coverage.lower() else 'checked', 'count': len(rows), 'coverage': coverage, 'warnings': warnings, 'checked_at': now()}
                except Exception as exc:
                    code = getattr(getattr(exc, 'response', None), 'status_code', None)
                    result['sources'][board] = {'state': getattr(exc, 'source_state', 'blocked' if code in (403, 429) else 'error'), 'error': f'{type(exc).__name__}' + (f' (HTTP {code})' if code else ''), 'checked_at': now(), 'diagnostic': getattr(exc, 'discovery_diagnostic', diagnose(exc))}
                with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?', (json.dumps(result), sid))
            result['phase'] = 'descriptions'
            with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(result),sid))
            result['descriptions'] = self.complete_descriptions(pending)
            result['phase'] = 'classifying'
            with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(result),sid))
            result['classification'] = self.jev.run([item for item in self.view(include_analysis=False)['items'] if item['id'] in pending], self.stop)
            if not self.stop.is_set() and self.settings()['auto_assess']:
                result['phase'] = 'assessing'
                with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(result),sid))
                self.assess_high_priority(pending)
            states = [source['state'] for source in result['sources'].values()]
            successful = sum(state in ('checked', 'partial') for state in states)
            result['state'] = ('interrupted' if self.stop.is_set() else
                               'failed' if not successful else
                               'incomplete' if successful < len(boards) or 'partial' in states else 'complete')
            result['coverage'] = {'responded': successful, 'total': len(boards)}
            result['phase'] = result['state']
        except Exception:
            result.update(state='error', phase='error', error='Discovery pipeline stopped. Saved results are retained.')
        finally:
            self.queued_ids.clear()
            with self.db() as db: db.execute('UPDATE scans SET finished=?,payload=? WHERE id=?', (now(), json.dumps(result), sid))

    def assess_high_priority(self, seen_ids):
        # Current-run sightings only; publication age does not imply poor fit.
        candidates = [item for item in self.view()['items'] if item['id'] in seen_ids]
        candidates.sort(key=lambda item: -item.get('keyword_match',{}).get('ranking',{}).get('points',0))
        self.queued_ids = {item['id'] for item in candidates if self.auto_eligible(item)}
        last_job = None
        for item in candidates:
            while not self.stop.is_set() and self.settings()['auto_assess'] and self.analysis.worker and self.analysis.worker.is_alive():
                self.stop.wait(2)
            if self.stop.is_set() or not self.settings()['auto_assess']: break
            if last_job:
                summary = self.assessment_summary({'job_id':last_job})
                if self.pause_on_provider_limit(summary.get('error','') + summary.get('recommendation_error','')): break
            # Context, target or job text may change while another job is running.
            current = next((v for v in self.view()['items'] if v['id']==item['id']), None)
            self.queued_ids.discard(item['id'])
            if not current or not self.auto_eligible(current): continue
            try:
                imported = self.import_role(item['id'], analyze=True, automatic=True)
                last_job = imported.get('job_id') if imported else None
            except Exception as exc:
                from .retrieval import RetrievalError
                error = str(exc) if isinstance(exc,(RetrievalError,Conflict,Missing)) else 'Automatic assessment stopped. Retry explicitly from the role.'
                with self.db() as db:
                    db.execute("UPDATE automatic_assessments SET state='FAILED',error=? WHERE opportunity_id=?",(error,item['id']))
                    db.execute('UPDATE opportunities SET assessment_state=? WHERE id=?',(error,item['id']))
                if self.pause_on_provider_limit(error): break
        self.queued_ids.clear()
        while not self.stop.is_set() and self.analysis.worker and self.analysis.worker.is_alive():
            self.stop.wait(2)
        if last_job and not self.stop.is_set():
            summary = self.assessment_summary({'job_id':last_job})
            self.pause_on_provider_limit(summary.get('error','') + summary.get('recommendation_error',''))

    @staticmethod
    def auto_eligible(item):
        return (item.get('keyword_match',{}).get('priority')=='high'
                and item.get('location_match',{}).get('status')=='match'
                and len(item.get('description',''))>=20
                and not item.get('description_changed')
                and not item.get('automatic_assessment'))

    def import_role(self, oid, analyze=True, automatic=False):
        with self.lock:
            with self.db() as db:
                row = db.execute('SELECT * FROM opportunities WHERE id=?', (oid,)).fetchone()
                if not row: raise Missing('Opportunity not found')
                item = json.loads(row['payload'])
                if len(item.get('description', '')) < 20: raise Conflict('Full description needed before assessment. Use Get full JD.')
                job_id = row['job_id']
                if analyze and job_id and row['imported_hash'] != self.content_hash(item):
                    raise Conflict('The listing changed. Review and save the updated JD before assessing it.')
                intent_id = oid + ':' + self.content_hash(item)
                if not job_id:
                    intent = db.execute('SELECT payload FROM import_intents WHERE id=?', (intent_id,)).fetchone()
                    data = JobInput.model_validate_json(intent[0]) if intent else JobInput(request_id=uuid5(NAMESPACE_URL, 'discovery:' + intent_id), company=item['company'][:200], title=item['title'][:200], description=item['description'], source_url=item['url'])
                    db.execute('INSERT OR IGNORE INTO import_intents VALUES(?,?)', (intent_id, data.model_dump_json()))
            if not job_id:
                # Reuse a manually saved job only when both URL and content agree.
                for existing in self.service.store.ids():
                    job = self.service.store.read(existing)[0]
                    if job.source_url and canonical(str(job.source_url)) == item['url'] and self.content_hash(job.model_dump()) == self.content_hash(data.model_dump()):
                        job_id = job.id; break
                if not job_id: job_id = self.service.create(data).job.id
                with self.db() as db: db.execute('UPDATE opportunities SET job_id=?,imported_hash=? WHERE id=?', (job_id, self.content_hash(data.model_dump()), oid))
            job = self.service.review(job_id).job
            if analyze:
                view = self.analysis.view(job)
                if automatic:
                    # Any prior report, stale result or unresolved attempt requires
                    # a user's decision, never an automatic paid retry.
                    if self.stop.is_set() or not self.settings()['auto_assess'] or view.get('run') or view.get('previous_result_outdated') or not view.get('ready'):
                        return {'job_id':job_id}
                    with self.db() as db:
                        claimed = db.execute("INSERT OR IGNORE INTO automatic_assessments VALUES(?, 'DISPATCHED', ?, '')",(oid,now())).rowcount
                    if not claimed: return {'job_id':job_id}
                if (view.get('run') or {}).get('state') not in ('COMPLETE', 'RUNNING'):
                    self.analysis.start(job, AnalyzeInput(consent_to_send=True, input_version=view['input_version']))
                with self.db() as db: db.execute('UPDATE opportunities SET assessment_state=? WHERE id=?', ('Assessment requested; open role for progress and score.', oid))
            return {'job_id': job_id}

    def begin_scheduler(self):
        def loop():
            while not self.stop.wait(20):
                try:
                    with self.lock:
                        cfg = self.settings(); slot = slot_at(datetime.now().astimezone())
                        if cfg['enabled'] and slot and slot != cfg.get('last_slot') and not (self.worker and self.worker.is_alive()):
                            self.start(); cfg['last_slot'] = slot
                            with self.db() as db: db.execute('UPDATE config SET payload=? WHERE id=1', (json.dumps(cfg),))
                except Exception:
                    # Transient database errors do not kill future scheduled checks.
                    continue
        self.scheduler = Thread(target=loop, daemon=True); self.scheduler.start()

    def close(self):
        self.stop.set()
        self.yc.close()
        if self.scheduler: self.scheduler.join(timeout=2)
        if self.worker: self.worker.join(timeout=25)
