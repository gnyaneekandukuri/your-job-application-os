"""Persistent local discovery queue and three-times-daily scheduler."""
from .read_snapshot import read_snapshot, snapshot_keyed
from .company_preferences import preferences as company_preferences, company_fit, apply_company_priority
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
from .discovery_sponsorship import no_sponsorship
from .discovery_keywords import KeywordIndex
from .discovery_jev import JevClassifier
from .models import JobInput
from .role_analysis import AnalyzeInput
from .store import Missing, Conflict
from .cost_tracking import scan_id, opportunity_scope


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
    def __init__(self, path, service, analysis, collector=collect, sources=lambda: [], skills=None):
        self.path, self.service, self.analysis, self.collector = path, service, analysis, collector
        self.lock, self.stop = RLock(), Event()
        self.yc = YCConnection(path.parent)
        if collector is collect:
            self.collector = self.collect_board
        self.worker = self.scheduler = None
        self.automatic_error = None
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
                CREATE TABLE IF NOT EXISTS scan_roles (scan_id INTEGER, opportunity_id TEXT, payload TEXT,
                PRIMARY KEY(scan_id,opportunity_id));
                CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT);
                CREATE TABLE IF NOT EXISTS import_intents (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automatic_assessments (opportunity_id TEXT PRIMARY KEY, state TEXT, attempted_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS automatic_versions (opportunity_id TEXT, input_version TEXT, attempted_at TEXT,
                PRIMARY KEY(opportunity_id,input_version));
                CREATE TABLE IF NOT EXISTS automatic_candidates (opportunity_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS discovery_drafts (id TEXT PRIMARY KEY, opportunity_id TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS description_fetches (opportunity_id TEXT PRIMARY KEY, state TEXT, checked_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS revisions (opportunity_id TEXT, content_hash TEXT, payload TEXT, seen_at TEXT,
                PRIMARY KEY(opportunity_id,content_hash));''')
            db.execute('INSERT OR IGNORE INTO config VALUES (1,?)', (json.dumps({'enabled': False, 'auto_assess': False, 'last_slot': slot_at(datetime.now().astimezone())}),))
            # A terminated process cannot still be scanning in this single-server application.
            for interrupted in db.execute('SELECT id,payload FROM scans WHERE finished IS NULL').fetchall():
                payload = json.loads(interrupted['payload'])
                payload.update(state='interrupted', error='Server stopped during this scan; saved listings were retained.')
                db.execute('UPDATE scans SET finished=?,payload=? WHERE id=?', (now(),json.dumps(payload),interrupted['id']))

        self.skills = skills
        self.keywords = KeywordIndex(path, sources)
        self.jev = JevClassifier(path)
        from .selected_assessments import SelectedQueue
        self.selected = SelectedQueue(self)
        from .job_retention import initialize
        with self.db() as db:
            initialize(db)
        from .job_dedup import consolidate
        with self.db() as db:
            consolidate(db)
        self.retention_checked_at = None

    def screening_profile(self, preferences):
        from .discovery_screening import candidate_profile
        from .retrieval import RetrievalError, chunk_story
        view = self.skills.view() if self.skills else {}
        try:
            passages = [{k:v for k,v in p.items() if k != 'raw_vector'} for p in self.analysis.passages()]
            if self.skills:
                # Resume-only candidates are supported too. Stories already appear
                # above; never add coaching or generated skill summaries as evidence.
                sources, _ = self.skills.snapshot()
                for source in sources:
                    if source['id'] not in ('current-resume', 'skills-resume'): continue
                    for chunk in chunk_story(source['text'], max_chunks=100):
                        passages.append({**chunk, 'id':source['id']+':'+chunk['id'],
                                         'source_title':source['title'],
                                         'source_caveats':['Listed resume claims; not independently verified.']})
                listed = list(dict.fromkeys(s['name'] for s in view.get('skills', [])
                              if not s.get('hidden') and any(r.get('id') in ('linkedin','manual') for r in s.get('sources', []))))
                for n, name in enumerate(listed):
                    passages.append({'id':'listed-skill:'+str(n), 'text':'Candidate lists this skill: '+name,
                                     'source_title':'Candidate-listed skills',
                                     'source_caveats':['A listed skill does not establish concrete delivery, ownership or outcomes.']})
            profile = candidate_profile(view, preferences, passages)
            # Optional private list of lead stories; not part of the screening cache identity.
            try:
                anchors = json.loads((self.analysis.library.root / 'knowledge/screening-anchors.json').read_text(encoding='utf-8'))
                profile['_anchors'] = tuple(anchors.get('anchor_stories', []))
            except (OSError, ValueError, AttributeError):
                profile['_anchors'] = ()
            return profile
        except RetrievalError as exc:
            return {**candidate_profile(view, preferences), 'error':str(exc)}

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
        if 'HTTP 429' not in error: return False
        with self.lock, self.db() as db:
            cfg = self.settings()
            provider = 'TypeSafe Jev' if 'Jev' in error or 'TypeSafe' in error else 'OpenAI'
            cfg.update(auto_assess=False, auto_assess_pause_reason=provider+' returned HTTP 429. Automatic analysis is paused; check that provider’s access, balance or rate limits before re-enabling it.')
            db.execute('UPDATE config SET payload=? WHERE id=1',(json.dumps(cfg),))
        return True

    @read_snapshot
    def assessment_results(self):
        """All saved deep analyses, including roles entered without discovery."""
        with self.db() as db:
            discovered = {r['job_id']: dict(r) for r in db.execute('SELECT * FROM opportunities WHERE job_id IS NOT NULL')}
            superseded = {r[0] for r in db.execute('SELECT job_id FROM superseded_jobs')}
            passed_saved = {r[0] for r in db.execute('SELECT job_id FROM passed_saved_jobs')}
            kept = {r[0] for r in db.execute('SELECT opportunity_id FROM kept_jobs')}
            for alias in db.execute('SELECT canonical_id,job_id FROM opportunity_aliases WHERE job_id IS NOT NULL'):
                primary = db.execute('SELECT * FROM opportunities WHERE id=?',(alias['canonical_id'],)).fetchone()
                if primary and alias['job_id'] not in discovered:
                    discovered[alias['job_id']] = {**dict(primary), 'imported_hash': None}
            yc_ids = {r[0] for r in db.execute("SELECT DISTINCT opportunity_id FROM sightings WHERE board='yc'")}
            scans = {r['opportunity_id']: dict(r) for r in db.execute('''
                SELECT sr.opportunity_id, s.id AS last_scan_id, s.started AS last_scanned_at
                FROM scan_roles sr JOIN scans s ON s.id=sr.scan_id
                WHERE sr.scan_id=(SELECT MAX(recent.scan_id) FROM scan_roles recent
                                  WHERE recent.opportunity_id=sr.opportunity_id)''')}
        items = []
        from .job_identity import same_job
        from .job_retention import age_state
        keys = {}
        for job_id in self.service.store.ids():
            job = self.saved_job(job_id)
            row = discovered.get(job_id)
            changed = bool(row and row['imported_hash'] and row['imported_hash'] != self.content_hash(json.loads(row['payload'])))
            # Results belong to the saved JD. A newer board snapshot must not
            # hide a completed report; disclose the difference beside it.
            summary = self.assessment_summary({'job_id':job_id})
            if changed:
                summary['listing_changed'] = True
                if summary['state'] == 'NOT_STARTED':
                    summary.update(state='JD_CHANGED', stage='Review the updated listing before assessment.')
            if summary['state'] == 'NOT_STARTED': continue
            if job_id in superseded and summary['state'] != 'COMPLETE': continue
            # Same 4-day window as New jobs: older analyses leave the list unless kept or in progress.
            if (row and row['id'] not in kept and summary['state'] not in ('RUNNING', 'QUEUED')
                    and age_state({**json.loads(row['payload']), 'first_seen': row['first_seen']}) == 'older_than_four_days'):
                continue
            if job_id in passed_saved and not summary.get('choice'): summary['choice'] = 'SKIP'
            keys[job_id] = same_job({'company': job.company, 'title': job.title, 'description': job.description})
            items.append({'yc_listing':bool(row and row['id'] in yc_ids),'job_id':job_id,'opportunity_id':row['id'] if row else None,'company':job.company,'title':job.title,'url':str(job.source_url or ''),'sponsorship':no_sponsorship(job.description),'assessment':summary, **(scans.get(row['id'], {}) if row else {})})
        # A stuck copy of a posting already analyzed in another location needs no help.
        analyzed = {keys.get(i['job_id']) for i in items if i['assessment']['state'] == 'COMPLETE'} - {None}
        items = [i for i in items if i['assessment']['state'] == 'COMPLETE' or keys.get(i['job_id']) not in analyzed]
        with self.db() as db:
            manual = {r[0] for r in db.execute("SELECT opportunity_id FROM selected_assessments WHERE state='QUEUED'")}
        counts = dict(complete=0, running=0, attention=0, previous=0)
        for item in items:
            summary = item['assessment']
            group = ('previous' if summary['state'] == 'STALE' else
                     'complete' if summary['state'] == 'COMPLETE' and summary.get('recommendation') else
                     'running' if summary['state'] == 'RUNNING' else 'attention')
            counts[group] += 1
        return {'items':items, 'counts':counts, 'queued_count':len(self.queued_ids | manual), 'settings':self.settings(), 'scans':self.run_reports()}

    def run_reports(self):
        with self.db() as db:
            reports=[]
            for row in db.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 5').fetchall():
                payload=scan_summary(json.loads(row['payload']))
                roles=[json.loads(r[0]) for r in db.execute('SELECT payload FROM scan_roles WHERE scan_id=?',(row['id'],))]
                reports.append({'id':row['id'],'started':row['started'],'finished':row['finished'],
                                'state':payload.get('state'),'new_count':payload.get('new_count'),
                                'error':payload.get('error'),'sources':payload.get('sources',{}),
                                'tracked':payload.get('tracking_version')==1,'roles':roles})
            return reports

    def record_scan_role(self, item, **updates):
        sid=scan_id.get()
        if sid is None: return
        with self.db() as db:
            row=db.execute('SELECT payload FROM scan_roles WHERE scan_id=? AND opportunity_id=?',(sid,item['id'])).fetchone()
            payload=json.loads(row[0]) if row else {'id':item['id'],'company':item.get('company',''),'title':item.get('title','')}
            payload.update(updates, updated_at=now())
            db.execute('INSERT OR REPLACE INTO scan_roles VALUES(?,?,?)',(sid,item['id'],json.dumps(payload)))

    @read_snapshot
    def view(self, include_analysis=True):
        preferences = load_preferences(self.analysis.library.root / "knowledge/discovery-preferences.json")
        company_policy = company_preferences(self.analysis.library.root, preferences)
        with self.db() as db:
            items = []
            locations = {}
            sightings = {}
            for source in db.execute('SELECT opportunity_id,board,source_id,seen_at,payload FROM sightings'):
                sightings.setdefault(source['opportunity_id'], []).append(dict(source))
            from .linkedin_source import reposted
            newest_linkedin = max((int(s['source_id']) for rows in sightings.values() for s in rows
                                   if s['board'] == 'linkedin' and str(s['source_id']).isdigit()), default=0)
            automatic = {r['opportunity_id']:dict(r) for r in db.execute('SELECT * FROM automatic_assessments')}
            selected_rows = {r['opportunity_id']:dict(r) for r in db.execute('SELECT opportunity_id,state,error FROM selected_assessments ORDER BY rowid')}
            for n, oid in enumerate([oid for oid, r in selected_rows.items() if r['state'] == 'QUEUED']):
                selected_rows[oid]['position'] = n + 1
            fetched_rows = {r['opportunity_id']:dict(r) for r in db.execute('SELECT * FROM description_fetches')}
            kept = {r[0] for r in db.execute('SELECT opportunity_id FROM kept_jobs')}
            passed = {r[0] for r in db.execute('SELECT opportunity_id FROM passed_jobs')}
            watched = {r[0] for r in db.execute('SELECT id FROM company_watchlist')}
            for row in db.execute('SELECT * FROM opportunities ORDER BY first_seen DESC'):
                item = json.loads(row['payload'])
                from .job_retention import age_state, company_key
                item['kept'] = row['id'] in kept
                item['passed'] = row['id'] in passed
                # An analysis alone does not keep a listing past 4 days; Keep job does.
                item['retention_protected'] = bool(row['id'] in kept or row['id'] in selected_rows)
                item['age_state'] = age_state({**item, 'first_seen': row['first_seen']})
                item['company_watched'] = company_key(item.get('company','')) in watched
                sources = sightings.get(row['id'], [])
                item.update(id=row['id'], first_seen=row['first_seen'], last_seen=row['last_seen'], job_id=row['job_id'],
                            assessment_state=row['assessment_state'], freshness=listing_freshness(item),
                            description_changed=bool(row['imported_hash'] and row['imported_hash'] != self.content_hash(item)),
                            sources=[{'board': s['board'], 'url': json.loads(s['payload']).get('url'), 'seen_at': s['seen_at'], 'posted_at': json.loads(s['payload']).get('posted_at')} for s in sources])
                # LinkedIn's fresh age on a reposted/promoted job is not its real age.
                if item.get('date_basis') == 'source_relative_age' and any(
                        s['board'] == 'linkedin' and reposted(s['source_id'], newest_linkedin) for s in sources):
                    item.update(freshness='older', linkedin_repost=True)
                location = item.get("location")
                if location not in locations:
                    locations[location] = location_match(location, preferences)
                item["location_match"] = dict(locations[location])
                item['sponsorship'] = no_sponsorship(item.get('description', ''))
                item["company_fit"] = company_fit(item, company_policy)
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
                attempt = automatic.get(row['id'])
                item['automatic_assessment'] = {k:attempt[k] for k in ('state','attempted_at','error')} if attempt else None
                item['queued_for_assessment'] = row['id'] in self.queued_ids
                selected = selected_rows.get(row['id'])
                item['selected_assessment'] = {k:selected.get(k) for k in ('state','error','position')} if selected else None
                fetched = fetched_rows.get(row['id'])
                item['description_fetch'] = {k:fetched[k] for k in ('state','checked_at','error')} if fetched else None
                items.append(item)
            scan = db.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
        # Never hold a listing cursor/read lock during filesystem-heavy analysis summaries.
        if include_analysis:
            for item in items:
                item["assessment"] = self.assessment_summary(item)
        keyword_profile = self.screening_profile(preferences) if include_analysis else None
        if include_analysis:
            self.jev.annotate(items)
            from .discovery_screening import annotate
            annotate(self.analysis.engine, items, keyword_profile)
            apply_company_priority(items)
            from .needs_help import needs_help
            for item in items: item['needs_help'] = needs_help(item)
        return {'company_preference':company_policy, 'jev': self.jev.status(), 'yc_connection': self.yc.status(), 'keyword_profile': keyword_profile, 'items': items, 'location_preference': preferences.get('location_filter', {}), 'settings': self.settings(), 'running': bool(self.worker and self.worker.is_alive()),
                'scan': {**dict(scan), 'payload': scan_summary(json.loads(scan['payload']))} if scan else None,
                'boards': BOARDS, 'unavailable': UNAVAILABLE, 'timezone': str(datetime.now().astimezone().tzinfo),
                'schedule': ['08:00', '13:00', '20:00']}

    @read_snapshot
    def table_view(self, include_assessments=False):
        """Only data displayed by the discovery UI; full evidence stays in local storage."""
        result = self.view()
        assessments = self.assessment_results()
        if include_assessments:
            result['assessment_results'] = assessments
        result['run_reports'] = assessments.get('scans', [])
        result['pipeline'] = {**assessments['counts'], 'queued':assessments['queued_count']}
        with self.db() as db:
            result['recent_scans'] = [{'started':r['started'], 'finished':r['finished'],
                                      **{k:v for k,v in scan_summary(json.loads(r['payload'])).items() if k in ('state','new_count')}}
                                     for r in db.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 5')]
        for item in result['items']:
            item.pop('description_provenance', None)
            item.pop('initial_evaluation', None)
            match = item.get('keyword_match')
            if match:
                item['keyword_match'] = {k:match.get(k) for k in ('priority','label','reason')}
                item['keyword_match']['experience'] = [{'term':entry['term']} for entry in match.get('experience', [])]
            classification = item.get('role_classification')
            if classification:
                item['role_classification'] = {k:classification.get(k) for k in ('labels','model','checked_at')}
        if result.get('keyword_profile'):
            result['keyword_profile'] = {'target':result['keyword_profile']['target']}
        return result

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
        return dict(self.saved_assessment(item['job_id']))

    @snapshot_keyed
    def saved_job(self, job_id):
        return self.service.store.read(job_id)[0]

    @snapshot_keyed
    def saved_assessment(self, job_id):
        from .retrieval import RetrievalError
        from .pursuit import PursuitService
        queued=self.analysis.engine.get('manual-analysis-queue:'+job_id)
        if queued and queued['state'] in ('QUEUED','NEEDS_ATTENTION'):
            return {'state':queued['state'],'stage':'Queued for deep analysis' if queued['state']=='QUEUED' else 'Analysis needs attention',
                    'error':queued.get('error',''),'score':None,'recommendation':None}
        try:
            # Polling must not reconcile the review graph or compete for its lock.
            job = self.saved_job(job_id)
            view = self.analysis.view(job, compact=True)
            run = view.get('run')
            if view.get('previous_result_outdated'):
                previous_score = view.get('previous_score') or {}
                return {'state':'STALE','score':previous_score.get('percent'),'recommendation':None,
                        'provisional':previous_score.get('provisional', True),
                        'stage':'Previous assessment saved. Sources, criteria or assessment method changed; open it to review or reassess.'}
            state = run['state'] if run else 'NOT_STARTED'
            score = view.get('score')
            result = {'assessed_at':run.get('updated_at') if run and state=='COMPLETE' else None,
                      'state':state,'stage':run.get('stage','') if run else '', 'error':run.get('error','') if run else '',
                      'score':score.get('percent') if score else None, 'provisional':bool(score and score.get('provisional')),
                      'recommendation':None,'choice':None}
            if state == 'COMPLETE':
                pursuit = PursuitService(self.analysis).view(job)
                result['recommendation'] = (pursuit.get('result') or {}).get('recommendation')
                result['explanation'] = (pursuit.get('result') or {}).get('explanation')
                result['rationale'] = (pursuit.get('result') or {}).get('rationale','')
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
            if changed and row['job_id']:
                # Unfinished work on the replaced description no longer needs the candidate's help.
                db.execute('INSERT OR IGNORE INTO superseded_jobs VALUES(?,?)', (row['job_id'], now()))
            db.execute('UPDATE opportunities SET payload=?,job_id=?,imported_hash=?,assessment_state=? WHERE id=?',
                       (json.dumps(item),None if changed else row['job_id'],None if changed else row['imported_hash'],None if changed else row['assessment_state'],oid))
            db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)',(oid,content_hash,json.dumps(item),now()))
        # The list projection refreshes in the background; rebuilding the full view here took ~30s.
        return {'id':oid,'saved':True,'content_hash':content_hash}

    @staticmethod
    def content_hash(item):
        return hashlib.sha256(json.dumps({k: item.get(k) for k in ('title', 'company', 'description')}, sort_keys=True).encode()).hexdigest()

    def save(self, board, item):
        from .job_dedup import source_reference
        item = dict(item); item['url'] = canonical(item['url'])
        oid = hashlib.sha256(item['url'].encode()).hexdigest()
        stamp = now()
        with self.lock, self.db() as db:
            retired = db.execute('SELECT id FROM seen_jobs WHERE id=? OR url=?', (oid,item['url'])).fetchone()
            retired_source = db.execute('SELECT opportunity_id FROM seen_sources WHERE board=? AND source_id=?', (board,item['source_id'])).fetchone()
            if retired or retired_source:
                retired_id = retired[0] if retired else retired_source[0]
                db.execute('INSERT OR REPLACE INTO seen_sources VALUES(?,?,?)', (board,item['source_id'],retired_id))
                return None, False
            previous = db.execute('SELECT opportunity_id FROM sightings WHERE board=? AND source_id=?', (board, item['source_id'])).fetchone()
            if previous: oid = previous[0]
            else:
                from .job_identity import identity
                key = identity(item)
                if key:
                    candidates = db.execute('SELECT id,payload FROM opportunities ORDER BY (job_id IS NOT NULL) DESC,first_seen,id').fetchall()
                    match = next((r for r in candidates if identity(json.loads(r['payload'])) == key), None)
                    if match: oid = match['id']
            row = db.execute('SELECT payload FROM opportunities WHERE id=?', (oid,)).fetchone()
            merged = item
            if row:
                old = json.loads(row[0])
                # A failed detail fetch or metadata-only source must not erase a good description.
                merged = {**old, **{k: v for k, v in item.items() if v not in ('', None, 'Unknown')}}
                # Date, label and provenance are one fact. A metadata-only sighting
                # must not relabel a previously saved date as a new posting claim.
                date_keys = ('posted_at', 'date_basis', 'date_label', 'fresh_until', 'older_after', 'age_observed_at')
                date_source = item if item.get('posted_at') or item.get('date_basis') == 'source_relative_age' else old
                for key in date_keys:
                    merged.pop(key, None)
                    if key in date_source:
                        merged[key] = date_source[key]
                if item.get('date_basis') == 'source_relative_age':
                    # A later unparseable age must not inherit an old freshness claim.
                    for key in ('fresh_until', 'older_after', 'age_observed_at'):
                        merged[key] = item.get(key)
            db.execute('INSERT INTO opportunities(id,payload,first_seen,last_seen) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen', (oid, json.dumps(merged), stamp, stamp))
            db.execute('INSERT OR REPLACE INTO sightings VALUES(?,?,?,?,?)', (board, item['source_id'], oid, json.dumps(source_reference(item)), stamp))
            db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)', (oid, self.content_hash(merged), json.dumps(merged), stamp))
        return oid, row is None

    def complete_descriptions(self, seen_ids):
        """Fetch missing JDs without a model; mismatches require manual review."""
        from .description_worker import extract_bounded
        from .safe_fetch import ExtractionError
        def identity(text):
            return re.sub(r'\b(inc|llc|ltd|company|corporation)\b', '', re.sub(r'[^a-z0-9]+',' ',text.lower())).strip()
        counts = {'fetched':0, 'needs_paste':0}
        items = [item for item in self.view(include_analysis=False)['items']
                 if item['id'] in seen_ids and len(item.get('description','')) < 200
                 and item['location_match']['status'] != 'outside']
        def progress(completed, current=None):
            sid = scan_id.get()
            if sid is None: return
            with self.db() as db:
                row = db.execute('SELECT payload FROM scans WHERE id=?', (sid,)).fetchone()
                if not row: return
                payload = json.loads(row[0])
                payload['description_progress'] = dict(total=len(items), completed=completed,
                    current=current, updated_at=now(), **counts)
                db.execute('UPDATE scans SET payload=? WHERE id=?', (json.dumps(payload), sid))
        for index, item in enumerate(items):
            if self.stop.is_set(): break
            progress(index, {'company': item['company'], 'title': item['title']})
            previous = item.get('description_fetch')
            if previous and freshness(previous['checked_at']) == 'within_24h':
                counts['needs_paste'] += 1
                continue
            try:
                draft = extract_bounded(item['url'])
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
        progress(counts['fetched'] + counts['needs_paste'])
        return counts

    def start(self, boards=None):
        with self.lock:
            if self.selected.worker and self.selected.worker.is_alive():
                raise Conflict('Selected assessments are running. Wait for them to finish before starting another scan.')
            if self.worker and self.worker.is_alive(): return self.view()
            self.worker = Thread(target=self.scan, args=(boards,), daemon=True)
            self.worker.start()
        return self.view()

    def scan(self, boards=None):
        include_leads = boards is None and self.collector == self.collect_board
        boards = list(boards) if boards is not None else list(BOARDS)
        if not boards or any(board not in BOARDS for board in boards):
            raise ValueError("Unknown discovery source")
        result = {'state': 'running', 'phase':'collecting', 'boards': boards, 'sources': {}, 'new_count': 0, 'cost_tracking_version': 1, 'tracking_version':1}
        with self.db() as db:
            sid = db.execute('INSERT INTO scans(started,payload) VALUES(?,?)', (now(), json.dumps(result))).lastrowid
        pending = set()
        scan_token = scan_id.set(sid)
        try:
            for board in boards:
                if self.stop.is_set(): break
                try:
                    rows, coverage, warnings = self.collector(board)
                    if self.stop.is_set(): break
                    for row in rows:
                        oid, new = self.save(board, row)
                        if oid is None:
                            result['previously_removed'] = result.get('previously_removed', 0) + 1
                            continue
                        pending.add(oid)
                        self.record_scan_role({**row,'id':oid}, stage='Discovered', newly_saved=new)
                        if new:
                            result['new_count'] += 1
                    result['sources'][board] = {'state': 'partial' if warnings or 'partial' in coverage.lower() or 'not established' in coverage.lower() else 'checked', 'count': len(rows), 'coverage': coverage, 'warnings': warnings, 'checked_at': now()}
                except Exception as exc:
                    code = getattr(getattr(exc, 'response', None), 'status_code', None)
                    result['sources'][board] = {'state': getattr(exc, 'source_state', 'blocked' if code in (403, 429) else 'error'), 'error': f'{type(exc).__name__}' + (f' (HTTP {code})' if code else ''), 'checked_at': now(), 'diagnostic': getattr(exc, 'discovery_diagnostic', diagnose(exc))}
                with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?', (json.dumps(result), sid))
            if include_leads and not self.stop.is_set():
                from .source_leads import refresh
                result['phase'] = 'company_leads'
                with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?', (json.dumps(result), sid))
                leads = refresh(self.path.parent, self.stop)
                result['lead_sources'] = {s['id']: {k: s.get(k) for k in ('state', 'checked_at', 'error')} for s in leads}
            result['phase'] = 'descriptions'
            with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(result),sid))
            result['descriptions'] = self.complete_descriptions(pending)
            from .job_dedup import consolidate, resolve
            with self.lock, self.db() as db:
                merged = consolidate(db)
                pending = {resolve(db, oid) for oid in pending}
            result['duplicates_consolidated'] = len(merged)
            result['phase'] = 'classifying'
            with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(result),sid))
            result['classification'] = self.jev.run([item for item in self.view(include_analysis=False)['items'] if item['id'] in pending and self.initial_screening_eligible(item)], self.stop)
            from .discovery_screening import screen
            preferences = load_preferences(self.analysis.library.root / 'knowledge/discovery-preferences.json')
            screening_items = [item for item in self.view(include_analysis=False)['items'] if item['id'] in pending and self.initial_screening_eligible(item)]
            screening_profile = self.screening_profile(preferences)
            self.jev.annotate(screening_items)
            result['screening'] = screen(self.analysis.engine, screening_items, screening_profile, self.stop)
            for item in self.view()['items']:
                if item['id'] in pending:
                    match=item.get('keyword_match',{})
                    self.record_scan_role(item,stage='Screening finished',priority=match.get('label','Not screened'),
                                          reason=match.get('reason',''),job_id=item.get('job_id'),
                                          assessment=item.get('assessment'),automatic_eligible=self.auto_eligible(item))
            if not self.stop.is_set() and self.settings()['auto_assess']:
                result['phase'] = 'assessing'
                with self.db() as db: db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(result),sid))
                self.assess_high_priority(pending)
            for item in self.view()['items']:
                if item['id'] in pending:
                    self.record_scan_role(item, job_id=item.get('job_id'), assessment=item.get('assessment'))
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
            scan_id.reset(scan_token)
            self.queued_ids.clear()
            with self.db() as db: db.execute('UPDATE scans SET finished=?,payload=? WHERE id=?', (now(), json.dumps(result), sid))

    def assess_high_priority(self, seen_ids):
        # Current-run sightings only; publication age does not imply poor fit.
        from .job_identity import same_job
        items = self.view()['items']
        # One paid analysis per identical posting, even when it is listed for several locations.
        taken, candidates = {same_job(i) for i in items if i.get('job_id')} - {None}, []
        for item in items:
            if item['id'] not in seen_ids or not self.auto_eligible(item): continue
            key = same_job(item)
            if key in taken: continue
            if key: taken.add(key)
            candidates.append(item)
        candidates.sort(key=lambda item: -item.get('keyword_match',{}).get('ranking',{}).get('points',0))
        self.queued_ids = {item['id'] for item in candidates if self.auto_eligible(item)}
        last_job = None
        for item in candidates:
            while not self.stop.is_set() and self.settings()['auto_assess'] and ((self.analysis.worker and self.analysis.worker.is_alive()) or (getattr(self.analysis,'manual_queue',None) and self.analysis.manual_queue.pending())):
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
                self.record_scan_role(current,stage='Checking automatic assessment')
                with opportunity_scope(item['id']):
                    imported = self.import_role(item['id'], analyze=True, automatic=True)
                last_job = imported.get('job_id') if imported else None
                started = bool(imported and imported.get('assessment_started'))
                self.record_scan_role(current,stage='Assessment started' if started else 'Assessment not started',
                                      assessment_requested=started,job_id=last_job)
            except Exception as exc:
                from .retrieval import RetrievalError
                error = str(exc) if isinstance(exc,(RetrievalError,Conflict,Missing)) else 'Automatic assessment stopped. Retry explicitly from the role.'
                self.record_scan_role(current,stage='Assessment blocked',error=error)
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
    def initial_screening_eligible(item):
        # Country exclusions precede both Jev passes. Company/stage preferences
        # rank matches and route automatic deep analysis, not initial screening.
        # Unclear location stays reviewable; deep analysis requires a match.
        # An explicit "no visa sponsorship" JD is ineligible: skip all paid model work.
        return item.get('location_match', {}).get('status') != 'outside' and not item.get('sponsorship')

    @staticmethod
    def auto_eligible(item):
        from .needs_help import description_problem
        return (item.get('keyword_match',{}).get('priority')=='high'
                and not description_problem(item.get('description',''))
                and not item.get('passed')
                and (item.get('age_state') != 'older_than_four_days' or item.get('retention_protected'))
                and (item.get('assessment') or {}).get('state') in (None, 'NOT_STARTED', 'STALE', 'JD_CHANGED')
                and item.get('location_match',{}).get('status')=='match'
                and not item.get('sponsorship')
                and item.get('company_fit',{}).get('automatic_allowed',True)
                and len(item.get('description',''))>=200
                and (not item.get('automatic_assessment') or
                     (item.get('assessment') or {}).get('state') in ('STALE', 'JD_CHANGED')))

    def import_role(self, oid, analyze=True, automatic=False):
        with self.lock:
            with self.db() as db:
                from .job_dedup import resolve
                oid = resolve(db, oid)
                row = db.execute('SELECT * FROM opportunities WHERE id=?', (oid,)).fetchone()
                if not row: raise Missing('Opportunity not found')
                item = json.loads(row['payload'])
                if len(item.get('description', '')) < 20: raise Conflict('Full description needed before assessment. Use Get full JD.')
                job_id = row['job_id']
                if analyze and job_id and row['imported_hash'] != self.content_hash(item):
                    if not automatic:
                        raise Conflict('The listing changed. Review and save the updated JD before assessing it.')
                    # New JD gets a new saved job; the previous report is retained.
                    job_id = None
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
            started = False
            if analyze:
                view = self.analysis.view(job)
                if automatic:
                    # Completed/current failed attempts are reused, never retried.
                    # A changed JD/context has a new input version and may run once.
                    if self.stop.is_set() or not self.settings()['auto_assess'] or view.get('run') or not view.get('ready'):
                        return {'job_id':job_id}
                    with self.db() as db:
                        claimed = db.execute('INSERT OR IGNORE INTO automatic_versions VALUES(?,?,?)',
                                             (oid,view['input_version'],now())).rowcount
                        if claimed:
                            db.execute("INSERT OR REPLACE INTO automatic_assessments VALUES(?, 'DISPATCHED', ?, '')",(oid,now()))
                    if not claimed: return {'job_id':job_id}
                if (view.get('run') or {}).get('state') not in ('COMPLETE', 'RUNNING'):
                    self.analysis.start(job, AnalyzeInput(consent_to_send=True, input_version=view['input_version']))
                    started = True
                with self.db() as db: db.execute('UPDATE opportunities SET assessment_state=? WHERE id=?', ('Assessment requested; open role for progress and score.', oid))
            return {'job_id': job_id, 'assessment_started': started}

    def rescreen(self, since, offset=0, limit=10):
        """Re-run initial screening for unsaved listings seen since a time; returns old vs new results."""
        from .discovery_screening import screen, annotate
        # Only a scan changes listings underneath; the automatic-analysis worker does not conflict.
        if self.worker and self.worker.is_alive() and self.worker.name != 'automatic-assessments':
            raise Conflict('A scan is running. Re-screen after it finishes.')
        preferences = load_preferences(self.analysis.library.root / 'knowledge/discovery-preferences.json')
        profile = self.screening_profile(preferences)
        with self.db() as db:
            passed = {r[0] for r in db.execute('SELECT opportunity_id FROM passed_jobs')}
        items = sorted((i for i in self.view(include_analysis=False)['items']
                        if (i.get('last_seen') or '') >= since and not i.get('job_id') and i['id'] not in passed
                        and self.initial_screening_eligible(i)), key=lambda i: i['id'])
        batch = items[offset:offset + limit]
        def summary(rows):
            annotate(self.analysis.engine, rows, profile)
            return [{'label': (r.get('keyword_match') or {}).get('label'),
                     'sources': [u.get('source_title') for u in ((r.get('initial_evaluation') or {}).get('retrieved') or [])]} for r in rows]
        before = summary([dict(i) for i in batch])
        counts = screen(self.analysis.engine, [dict(i) for i in batch], profile, self.stop, force=True)
        after = summary([dict(i) for i in batch])
        return {'total': len(items), 'offset': offset, 'counts': counts,
                'rows': [{'company': i['company'], 'title': i['title'], 'before': b, 'after': a}
                         for i, b, a in zip(batch, before, after)]}

    def reconcile_automatic(self):
        """Dispatch saved High priority jobs without waiting for another scan."""
        from .discovery_screening import screen
        try:
            self.automatic_error = None
            items = self.view()['items']
            with self.db() as db:
                tracked = {r[0] for r in db.execute('SELECT opportunity_id FROM automatic_candidates')}
                # Bootstrap from recorded scan decisions, preserving old reports.
                for row in db.execute('SELECT opportunity_id,payload FROM scan_roles'):
                    if json.loads(row[1]).get('priority') == 'High priority': tracked.add(row[0])
                tracked.update(i['id'] for i in items if i.get('keyword_match',{}).get('priority') == 'high')
                db.executemany('INSERT OR IGNORE INTO automatic_candidates VALUES(?)', [(oid,) for oid in tracked])
            changed = [i for i in items if i['id'] in tracked and
                       i.get('keyword_match',{}).get('label') == 'Not screened' and self.initial_screening_eligible(i)]
            if changed:
                preferences = load_preferences(self.analysis.library.root / 'knowledge/discovery-preferences.json')
                screen(self.analysis.engine, changed, self.screening_profile(preferences), self.stop)
            self.assess_high_priority(tracked)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Automatic assessment queue stopped')
            self.automatic_error = 'Automatic analysis stopped unexpectedly. Saved work is retained; review before enabling it again.'
            with self.lock, self.db() as db:
                config = self.settings()
                config['auto_assess'] = False
                db.execute('UPDATE config SET payload=? WHERE id=1', (json.dumps(config),))
        finally:
            self.queued_ids.clear()

    def start_automatic(self):
        with self.lock:
            if (self.stop.is_set() or not self.settings()['auto_assess'] or
                    (self.worker and self.worker.is_alive()) or
                    (self.selected.worker and self.selected.worker.is_alive()) or
                    (self.analysis.worker and self.analysis.worker.is_alive())):
                return
            self.worker = Thread(target=self.reconcile_automatic, daemon=True, name='automatic-assessments')
            self.worker.start()

    def begin_scheduler(self):
        self.selected.start()
        def loop():
            while not self.stop.wait(20):
                try:
                    self.selected.start()
                    with self.lock:
                        cfg = self.settings(); slot = slot_at(datetime.now().astimezone())
                        if cfg['enabled'] and slot and slot != cfg.get('last_slot') and not (self.worker and self.worker.is_alive()) and not (self.selected.worker and self.selected.worker.is_alive()):
                            self.start(); cfg['last_slot'] = slot
                            with self.db() as db: db.execute('UPDATE config SET payload=? WHERE id=1', (json.dumps(cfg),))
                    with self.lock:
                        current = datetime.now(timezone.utc)
                        idle = not any(w and w.is_alive() for w in (self.worker, self.selected.worker, self.analysis.worker))
                        if idle and (self.retention_checked_at is None or (current-self.retention_checked_at).total_seconds() >= 3600):
                            from .job_retention import cleanup
                            cleanup(self, current)
                            self.retention_checked_at = current
                    self.start_automatic()
                except Exception:
                    # Transient database errors do not kill future scheduled checks.
                    continue
        self.scheduler = Thread(target=loop, daemon=True); self.scheduler.start()

    def close(self):
        self.stop.set()
        self.yc.close()
        if self.scheduler: self.scheduler.join(timeout=2)
        if self.worker: self.worker.join(timeout=25)
        if self.selected.worker: self.selected.worker.join(timeout=2)
