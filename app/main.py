import os
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, Response
from . import team_message
from .google_sheets import GoogleSheets, ClientConfig, SheetTarget
from .applications import Applications, ConfirmApplication, UpdateApplication, ManualApplication, ExternalApplication
from .job_retention import KeepJob, PassJob, Rescreen, WatchCompany, keep, keep_saved_job, pass_job, pass_saved_job, watch, watchlist, status as retention_status
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import DecisionInput, ExtractionDraft, ExtractionInput, JobInput, Profile, Review, VersionInput
from .extraction import extract_job
from .safe_fetch import ExtractionError
from .service import ReviewService
from .store import Conflict, Missing, Store
from .workflow import build_graph
from .retrieval import BuildInput, SearchInput, RetrievalService, RetrievalError, SOURCE
from .model_provider import OpenAIProvider
from .tailoring import TailoringPreferences, TailoringResumeUpload, Tailoring, Generate as TailorGenerate, Edit as TailorEdit, Action as TailorAction, ProposalDecision as TailorProposalDecision
from fastapi.responses import PlainTextResponse
from .job_requirements import ExtractRequirements
from .jev_requirements import extract as extract_requirements, key_for as jev_requirements_key
from .requirement_evidence import EvidenceInput
from .role_analysis import RoleAnalysis, AnalyzeInput
from .evidence_workflow import ReviewDecision
from .pursuit import PursuitService, Generate as GeneratePursuit, Choose as ChoosePursuit
from .story_library import StoryLibrary, AddStory, EditStory, PrepareStory, LibrarySearch
from .document_library import (UploadDocument, SaveAndPrepare, AddAndPrepare, Consent,
                               upload_document, prepare_saved, import_coaching, context_pack)

from .experience_additions import ExperienceAddition, save_addition, retry as retry_addition

from . import review_conversation as review_chat
from . import feedback_learning as learning
from .selected_assessments import SelectedAssessments
from .discovery import Discovery, Settings as DiscoverySettings, DescriptionInput
from .discovery_keywords import TargetInput, library_sources
from .discovery_preferences import load_preferences
from .skills_profile import SkillsProfile, BuildSkills, ResumeInput, LinkedInInput, SkillEdit
from threading import Thread
from .user_workspace import initialize, SetupInput, save_setup, setup_view, default_data_directory

ROOT = Path(__file__).resolve().parents[1]


def application_posted_times(discovery_path):
    """Read board timestamps only, never scanner first_seen or relative age labels."""
    from .application_stats import precise_timestamp
    from contextlib import closing
    candidates = {}
    with closing(sqlite3.connect(Path(discovery_path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute("SELECT job_id,payload FROM opportunities WHERE job_id IS NOT NULL "
                          "UNION ALL SELECT o.job_id,s.payload FROM sightings s "
                          "JOIN opportunities o ON o.id=s.opportunity_id WHERE o.job_id IS NOT NULL")
        for job_id, payload in rows:
            value = json.loads(payload).get('posted_at')
            if value:
                candidates.setdefault(job_id, []).append(value)
    result = {}
    for job_id, values in candidates.items():
        absolute = [(precise_timestamp(value), value) for value in values if precise_timestamp(value)]
        result[job_id] = min(absolute, key=lambda pair: pair[0])[1] if absolute else values[0]
    return result


def create_app(data_dir: Path | None = None):
    private_dir = data_dir or default_data_directory()

    workspace = private_dir / "workspace"
    initialize(workspace)

    @asynccontextmanager
    async def lifespan(api):
        store = Store(private_dir / "reviews.sqlite3")
        api.state.applications = Applications(private_dir / "applications.sqlite3")
        api.state.google_sheets = GoogleSheets(private_dir, api.state.applications)
        connection = sqlite3.connect(private_dir / "checkpoints.sqlite3", check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        saver = SqliteSaver(connection)
        saver.setup()
        api.state.service = ReviewService(store, build_graph(saver))
        api.state.retrieval = RetrievalService(private_dir / "retrieval.sqlite3", workspace / SOURCE, OpenAIProvider(allow_generation=False, allow_embeddings=True))
        api.state.library = StoryLibrary(workspace, private_dir, OpenAIProvider(allow_generation=False, allow_embeddings=True))
        api.state.skills = SkillsProfile(api.state.library, OpenAIProvider(allow_generation=True, allow_embeddings=False))
        if data_dir is None:
            Thread(target=api.state.skills.watch, daemon=True).start()
        api.state.analysis = RoleAnalysis(api.state.retrieval, api.state.library)
        api.state.analysis.skills = api.state.skills
        from .manual_analysis_queue import ManualAnalysisQueue
        api.state.manual_queue = ManualAnalysisQueue(api.state.analysis, lambda job_id: store.read(job_id)[0])
        api.state.analysis.manual_queue = api.state.manual_queue
        api.state.manual_queue.start()
        api.state.tailoring = Tailoring(api.state.analysis, api.state.skills, OpenAIProvider(allow_generation=True, allow_embeddings=False, request_timeout=120))
        api.state.discovery = Discovery(private_dir / "discovery.sqlite3", api.state.service, api.state.analysis, sources=lambda: library_sources(api.state.library), skills=api.state.skills)
        from .resume_approach import ResumeApproach
        api.state.resume_approach = ResumeApproach(api.state.tailoring, api.state.discovery)
        api.state.analysis.resume_approach = api.state.resume_approach
        api.state.tailoring.resume_approach = api.state.resume_approach
        from .job_list import JobList
        api.state.job_list = JobList(api.state.discovery)
        api.state.discovery.job_list = api.state.job_list
        if data_dir is None:
            api.state.job_list.start()
            api.state.discovery.begin_scheduler()
        for job_id in store.ids():
            api.state.service.review(job_id)
        if data_dir is None: api.state.google_sheets.schedule.start()
        from .application_stats import SavedPriorities
        from .read_snapshot import read_snapshot
        @read_snapshot
        def saved_priorities(job_ids):
            # One read snapshot shares library/retrieval inputs across all jobs.
            priorities = {}
            for job_id in job_ids:
                assessment = api.state.discovery.saved_assessment(job_id)
                priorities[job_id] = assessment.get('choice') or assessment.get('recommendation')
            return priorities
        api.state.stats_priorities = SavedPriorities(saved_priorities)
        if data_dir is None:
            with api.state.applications.db() as db:
                applied = {json.loads(row[0])['job_id'] for row in db.execute('SELECT payload FROM applications')}
            api.state.stats_priorities.refresh({j for j in applied if not j.startswith(('external:', 'opportunity:'))})
        try:
            yield
        finally:
            api.state.google_sheets.schedule.close()
            api.state.resume_approach.close()
            api.state.manual_queue.close()
            api.state.job_list.close()
            api.state.skills.close()
            api.state.discovery.close()
            api.state.tailoring.close()
            api.state.analysis.close()
            connection.close()

    api = FastAPI(title="Job Review · Local prototype", version="0.2.0", lifespan=lifespan)
    api.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @api.middleware("http")
    async def local_guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            # A custom header cannot be sent by a cross-site HTML form. No CORS is enabled.
            if request.headers.get("x-local-review") != "1":
                return JSONResponse({"detail": "Local review header required."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin writes are not allowed."}, status_code=403)
            limit = 2_800_000 if request.url.path in ("/api/library/upload", "/api/library/skills/resume", "/api/tailoring/resume") else 300000
            if len(await request.body()) > limit:
                return JSONResponse({"detail": "Request too large."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'")
        return response

    @api.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @api.exception_handler(Missing)
    async def missing_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @api.exception_handler(ExtractionError)
    async def extraction_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @api.get("/api/discovery")
    def discovery_status(request: Request):
        return request.app.state.job_list.overview()

    @api.get("/api/maintenance/status")
    def maintenance_status(request: Request):
        from .maintenance import activity
        return activity(request.app.state)

    @api.get("/api/discovery/leads")
    def discovery_leads(request: Request):
        from .source_leads import view
        return view(request.app.state.discovery.path.parent)

    @api.get("/api/discovery/status")
    def job_list_status(request: Request):
        return request.app.state.job_list.status()

    @api.get("/api/discovery/watchlist")
    def company_watchlist(request: Request):
        return watchlist(request.app.state.discovery)

    @api.get("/api/discovery/retention")
    def retention_info(request: Request):
        return retention_status(request.app.state.discovery)

    @api.post("/api/discovery/watchlist")
    def save_company_watchlist(data: WatchCompany, request: Request):
        return watch(request.app.state.discovery, data)

    @api.post("/api/assessments/{job_id}/pass")
    def pass_saved(job_id: str, data: PassJob, request: Request):
        request.app.state.service.review(job_id)
        return pass_saved_job(request.app.state.discovery, job_id, data.passed)

    @api.post("/api/discovery/rescreen")
    def discovery_rescreen(data: Rescreen, request: Request):
        return request.app.state.discovery.rescreen(data.since, data.offset, data.limit)

    @api.post("/api/discovery/{opportunity_id}/pass")
    def pass_opportunity(opportunity_id: str, data: PassJob, request: Request):
        return pass_job(request.app.state.discovery, opportunity_id, data.passed)

    @api.post("/api/assessments/{job_id}/keep")
    def keep_assessed_job(job_id: str, data: KeepJob, request: Request):
        return keep_saved_job(request.app.state.discovery, job_id, data.keep)

    @api.post("/api/discovery/{opportunity_id}/keep")
    def keep_opportunity(opportunity_id: str, data: KeepJob, request: Request):
        return keep(request.app.state.discovery, opportunity_id, data.keep)

    @api.get("/api/discovery/page")
    def job_list_page(request: Request, page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100),
                      query: str = Query('', max_length=200), priority: str = 'all', location: str = 'match',
                      company: str = 'focus', board: str = 'all', show: str = 'all', added: str = Query('all', pattern='^(all|today|yesterday|last4|last_scan)$'), runs: bool = False, view: str = '', runs_page: int = Query(1, ge=1), runs_limit: int = Query(10, ge=1, le=50)):
        if runs or view == 'runs':
            return request.app.state.job_list.runs(runs_page, runs_limit)
        return request.app.state.job_list.page(page,size,query,priority,location,company,board,show,runs or view == 'runs',added=added)

    @api.get("/api/discovery/runs/{scan_id}")
    def discovery_run(scan_id: int, request: Request):
        return request.app.state.job_list.run(scan_id)

    @api.get("/api/discovery/{opportunity_id}/detail")
    def job_list_detail(opportunity_id: str, request: Request):
        return request.app.state.job_list.detail(opportunity_id)

    @api.post("/api/discovery/yc/connect")
    def yc_connect(request: Request):
        return request.app.state.discovery.yc.start('login')

    @api.post("/api/discovery/yc/scan")
    def yc_scan(request: Request):
        return request.app.state.discovery.start(['yc'])

    @api.post("/api/discovery/yc/check")
    def yc_check(request: Request):
        return request.app.state.discovery.yc.start('check')

    @api.post("/api/discovery/lenny/connect")
    def lenny_connect(request: Request):
        return request.app.state.discovery.lenny.start('login')

    @api.post("/api/discovery/lenny/check")
    def lenny_check(request: Request):
        return request.app.state.discovery.lenny.start('check')

    @api.post("/api/discovery/lenny/scan")
    def lenny_scan(request: Request):
        return request.app.state.discovery.start(['lenny'])

    @api.post("/api/discovery/linkedin/scan")
    def linkedin_scan(request: Request):
        return request.app.state.discovery.start(['linkedin'])

    @api.get("/api/ready-to-apply")
    def ready_to_apply(request: Request):
        state = request.app.state
        from contextlib import closing
        with closing(state.retrieval.connect()) as db:
            ids = [k.removeprefix('tailoring:latest:') for (k,) in db.execute("SELECT key FROM cache WHERE key LIKE 'tailoring:latest:%'")]
        applied = {row.get('job_id') for row in state.applications.list()}
        scores = {row['job_id']: row for row in state.job_list.assessments().get('items', [])}
        return {'items': state.tailoring.ready(state.service.store.read_jobs(ids), scores, applied)}

    @api.get("/api/assessments/saved")
    def saved_assessment_results(request: Request):
        result = request.app.state.job_list.assessments()
        resumes = request.app.state.tailoring.statuses([r['job_id'] for r in result.get('items', [])])
        jobs = request.app.state.service.store.read_jobs([r['job_id'] for r in result.get('items', []) if r['assessment']['state']=='COMPLETE'])
        approaches = request.app.state.resume_approach.views(jobs.values())
        for item in result.get('items', []):
            if item['job_id'] in resumes: item['resume'] = resumes[item['job_id']]
            if item['assessment']['state'] == 'COMPLETE':
                item['resume_approach'] = approaches.get(item['job_id'], {'state':'UNAVAILABLE','reason':'Saved job not found.'})
            else:
                item['resume_approach'] = {'state': 'UNAVAILABLE', 'reason': 'Complete a current deep analysis first.'}
        return result

    @api.post("/api/jobs/{job_id}/resume-approach")
    def check_resume_approach(job_id: str, request: Request):
        discovery = request.app.state.discovery
        if discovery.saved_assessment(job_id).get('state') != 'COMPLETE':
            raise RetrievalError('Complete a current deep analysis first.', 409)
        return request.app.state.resume_approach.enqueue(discovery.saved_job(job_id), retry=True)

    @api.post("/api/assessments/resume-approaches")
    def check_saved_resume_approaches(request: Request):
        state = request.app.state
        queued, skipped = 0, 0
        for item in state.job_list.assessments().get('items', []):
            if item['assessment']['state'] != 'COMPLETE' or item['assessment'].get('choice') == 'SKIP':
                continue
            try:
                job = state.discovery.saved_job(item['job_id'])
                current = state.resume_approach.view(job)
                if current['state'] == 'NOT_CHECKED':
                    state.resume_approach.enqueue(job)
                    queued += 1
            except RetrievalError:
                skipped += 1
        return {'queued': queued, 'unavailable': skipped}

    @api.get("/api/assessments")
    def assessment_results(request: Request):
        return request.app.state.discovery.assessment_results()

    @api.get("/api/library/skills")
    def skills_status(request: Request):
        return request.app.state.skills.view()

    @api.post("/api/library/skills/build")
    def skills_build(data: BuildSkills, request: Request):
        return request.app.state.skills.start()

    @api.post("/api/library/skills/resume")
    def skills_resume(data: ResumeInput, request: Request):
        return request.app.state.skills.upload_resume(data)

    @api.post("/api/library/skills/resume/remove")
    def skills_remove_resume(request: Request):
        return request.app.state.skills.remove_resume()

    @api.post("/api/library/skills/linkedin")
    def skills_linkedin(data: LinkedInInput, request: Request):
        return request.app.state.skills.linkedin(data)

    @api.post("/api/library/skills/edit")
    def skills_edit(data: SkillEdit, request: Request):
        return request.app.state.skills.edit(data)

    @api.get("/api/library/keywords")
    def library_keywords(request: Request):
        return request.app.state.discovery.keywords.enrich([], load_preferences(workspace / "knowledge/discovery-preferences.json"))

    @api.post("/api/discovery/target")
    def discovery_target(data: TargetInput, request: Request):
        discovery = request.app.state.discovery
        discovery.keywords.update_target(data, load_preferences(workspace / "knowledge/discovery-preferences.json"))
        current = setup_view(workspace)
        current.pop("complete")
        current["target_roles"] = data.roles
        save_setup(workspace, SetupInput(**current))
        return discovery.view()

    @api.post("/api/discovery/check")
    def discovery_check(request: Request):
        return request.app.state.discovery.start()

    @api.post("/api/discovery/backlog")
    def discovery_backlog(request: Request):
        return request.app.state.discovery.start_backlog()

    @api.get("/api/usage")
    def api_usage():
        from .usage_dashboard import usage_report
        return usage_report(private_dir)

    @api.post("/api/discovery/settings")
    def discovery_settings(data: DiscoverySettings, request: Request):
        return request.app.state.discovery.configure(data)

    @api.post("/api/discovery/{opportunity_id}/description/fetch")
    def discovery_fetch_description(opportunity_id: str, request: Request):
        return request.app.state.discovery.fetch_description(opportunity_id)

    @api.post("/api/discovery/{opportunity_id}/description")
    def discovery_save_description(opportunity_id: str, data: DescriptionInput, request: Request):
        return request.app.state.discovery.save_description(opportunity_id, data)

    @api.post("/api/discovery/assess-selected")
    def discovery_assess_selected(data: SelectedAssessments, request: Request):
        return request.app.state.discovery.selected.enqueue(data)

    @api.post("/api/discovery/{opportunity_id}/assess")
    def discovery_assess(opportunity_id: str, request: Request):
        return request.app.state.discovery.import_role(opportunity_id)

    @api.post("/api/discovery/{opportunity_id}/save")
    def discovery_save(opportunity_id: str, request: Request):
        """Save the listing as a role for writing help; no analysis or model call."""
        return request.app.state.discovery.import_role(opportunity_id, analyze=False)

    @api.get("/api/setup")
    def get_setup():
        return setup_view(workspace)

    @api.post("/api/setup")
    def set_setup(data: SetupInput, request: Request):
        result = save_setup(workspace, data)
        index = request.app.state.discovery.keywords
        from contextlib import closing
        with index.lock, closing(index.connect()) as db:
            target = index.target(db, load_preferences(workspace / "knowledge/discovery-preferences.json"))
        index.update_target(TargetInput(roles=result["target_roles"], keywords=target["keywords"], expected_version=target["version"]), load_preferences(workspace / "knowledge/discovery-preferences.json"))
        return result

    @api.get("/api/health")
    def health():
        return {"status": "ok", "mode": "sample-only", "schema_version": 2}

    @api.exception_handler(RetrievalError)
    async def retrieval_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @api.get("/api/retrieval")
    def retrieval_status(request: Request):
        return request.app.state.retrieval.status()

    @api.get("/api/library")
    def library_status(request: Request):
        return request.app.state.library.status()

    @api.post("/api/library/search")
    def library_search(data: LibrarySearch, request: Request):
        return request.app.state.library.search(data)

    @api.post("/api/library/upload")
    def upload(data: UploadDocument, request: Request):
        result = upload_document(request.app.state.library, data)
        if result["source"]["category"] == "story":
            request.app.state.skills.start()
        return result

    @api.post("/api/library/add-and-prepare")
    def add_and_prepare(data: AddAndPrepare, request: Request):
        library = request.app.state.library
        with library.lock:
            source = library.add(AddStory(**data.model_dump(exclude={"consent_to_send"})))
            result = prepare_saved(library, source)
        if result["source"]["category"] == "story":
            request.app.state.skills.start()
        return result

    @api.post("/api/library/sources/{source_id}/save-and-prepare")
    def save_and_prepare(source_id: str, data: SaveAndPrepare, request: Request):
        library = request.app.state.library
        with library.lock:
            source = library.edit(source_id, EditStory(**data.model_dump(exclude={"consent_to_send"})))
            result = prepare_saved(library, source)
        if result["source"]["category"] == "story":
            request.app.state.skills.start()
        return result

    @api.post("/api/library/import-coaching")
    def import_existing_coaching(data: Consent, request: Request):
        return {"results": import_coaching(request.app.state.library)}

    @api.get("/api/library/context-pack")
    def full_context():
        return context_pack(workspace)

    @api.post("/api/library/sources", status_code=201)
    def add_story(data: AddStory, request: Request):
        return request.app.state.library.add(data)

    @api.get("/api/library/sources/{source_id}")
    def story_detail(source_id: str, request: Request):
        return request.app.state.library.detail(source_id)

    @api.post("/api/library/sources/{source_id}")
    def edit_story(source_id: str, data: EditStory, request: Request):
        return request.app.state.library.edit(source_id, data)

    @api.post("/api/library/sources/{source_id}/prepare")
    def prepare_story(source_id: str, data: PrepareStory, request: Request):
        return request.app.state.library.prepare(source_id, data)

    @api.post("/api/retrieval/build")
    def retrieval_build(data: BuildInput, request: Request):
        return request.app.state.retrieval.build(data)

    @api.post("/api/retrieval/search")
    def retrieval_search(data: SearchInput, request: Request):
        return request.app.state.retrieval.search(data)

    @api.post("/api/extractions", response_model=ExtractionDraft, status_code=201)
    def extract(data: ExtractionInput, request: Request):
        draft = extract_job(data.url)
        request.app.state.service.store.save_extraction(draft)
        return draft

    @api.get("/api/extractions/{extraction_id}", response_model=ExtractionDraft)
    def extracted(extraction_id: str, request: Request):
        return request.app.state.service.store.extraction(extraction_id)

    @api.get("/api/profile", response_model=Profile)
    def profile(request: Request):
        return request.app.state.service.store.profile()

    @api.get("/api/jobs", response_model=list[Review])
    def jobs(request: Request):
        service = request.app.state.service
        return [service.review(job_id) for job_id in service.store.ids()]

    @api.post("/api/jobs", response_model=Review, status_code=201)
    def create_job(data: JobInput, request: Request):
        return request.app.state.service.create(data)

    @api.get("/api/jobs/{job_id}", response_model=Review)
    def get_job(job_id: str, request: Request):
        return request.app.state.service.review(job_id)

    @api.post("/api/jobs/{job_id}/decision", response_model=Review)
    def decide(job_id: str, data: DecisionInput, request: Request):
        return request.app.state.service.decide(job_id, data)

    @api.get("/api/jobs/{job_id}/requirements")
    def get_requirements(job_id: str, request: Request):
        job = request.app.state.service.review(job_id).job
        from .job_requirements import source_hash
        engine = request.app.state.retrieval
        return {'source_sha256':source_hash(job), 'result':engine.get(jev_requirements_key(job)), 'usage':engine.usage()}

    @api.post("/api/jobs/{job_id}/requirements")
    def requirements(job_id: str, data: ExtractRequirements, request: Request):
        job = request.app.state.service.review(job_id).job
        return extract_requirements(request.app.state.retrieval, job, data)

    @api.post("/api/jobs/{job_id}/sample-version", response_model=Review)
    def revise(job_id: str, data: VersionInput, request: Request):
        return request.app.state.service.revise(job_id, data)

    @api.get("/api/jobs/{job_id}/requirements/{requirement_id}/evidence")
    def get_evidence(job_id: str, requirement_id: str, request: Request):
        job = request.app.state.service.review(job_id).job
        view = request.app.state.analysis.view(job)
        return {'context_version':view['input_version'], 'ready':view['ready'], 'unready_sources':view['unready_sources'],
                'result':(view['run'] or {}).get('results', {}).get(requirement_id), 'usage':view['usage']}

    @api.get("/api/applications")
    def applications_list(request: Request):
        return request.app.state.applications.list()

    @api.get("/api/applications/stats")
    def applications_stats(request: Request):
        from .application_stats import application_stats
        store = request.app.state.applications
        with store.db() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT payload FROM applications')]
        # Reuse saved job recommendations. No generation, scan, or status mutations.
        priorities = request.app.state.stats_priorities.get({record['job_id'] for record in records})
        return application_stats(records, priorities, application_posted_times(request.app.state.discovery.path))

    @api.get("/api/applications/sheets")
    def sheets_status(request: Request):
        return request.app.state.google_sheets.status()

    @api.post("/api/applications/sheets/configure")
    def sheets_configure(data: ClientConfig, request: Request):
        return request.app.state.google_sheets.configure(data)

    @api.post("/api/applications/sheets/connect")
    def sheets_connect(request: Request):
        return request.app.state.google_sheets.connect()

    @api.get("/api/applications/sheets/callback")
    def sheets_callback(request: Request, state: str = '', code: str = '', error: str = ''):
        from html import escape
        from fastapi.responses import HTMLResponse
        try:
            request.app.state.google_sheets.callback(state, code, error)
            message = 'Google connected. You can close this tab and return to Applied jobs to choose your spreadsheet.'
            status = 200
        except RetrievalError as exc:
            message, status = str(exc), exc.status
        return HTMLResponse('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Google Sheets connection</title><h1>Google Sheets</h1><p>' + escape(message) + '</p>', status_code=status,
                            headers={'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'none'"})

    @api.post("/api/applications/sheets/target")
    def sheets_target(data: SheetTarget, request: Request):
        return request.app.state.google_sheets.set_target(data)

    @api.post("/api/applications/sheets/sync")
    def sheets_sync(request: Request):
        return request.app.state.google_sheets.sync()

    @api.post("/api/applications/sheets/disconnect")
    def sheets_disconnect(request: Request):
        return request.app.state.google_sheets.disconnect()

    @api.get("/api/applications/export")
    def applications_export(request: Request):
        return Response(request.app.state.applications.export(),
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename="applied-jobs.xlsx"'})

    @api.post("/api/jobs/{job_id}/applications")
    def application_confirm(job_id: str, data: ConfirmApplication, request: Request):
        job = request.app.state.service.review(job_id).job
        metadata = {}
        with request.app.state.discovery.db() as db:
            row = db.execute('SELECT id,payload FROM opportunities WHERE job_id=?', (job_id,)).fetchone()
            if row:
                metadata['location'] = json.loads(row['payload']).get('location', '')
                metadata['boards'] = [r[0] for r in db.execute('SELECT DISTINCT board FROM sightings WHERE opportunity_id=?', (row['id'],))]
        return request.app.state.applications.confirm(job, request.app.state.tailoring, data, metadata)

    @api.post("/api/jobs/{job_id}/applications/manual")
    def application_manual(job_id: str, data: ManualApplication, request: Request):
        job = request.app.state.service.review(job_id).job
        metadata = {}
        with request.app.state.discovery.db() as db:
            row = db.execute('SELECT id,payload FROM opportunities WHERE job_id=?', (job_id,)).fetchone()
            if row:
                metadata['location'] = json.loads(row['payload']).get('location','')
                metadata['boards'] = [r[0] for r in db.execute('SELECT DISTINCT board FROM sightings WHERE opportunity_id=?',(row['id'],))]
        return request.app.state.applications.confirm_without_resume(job_id,job.model_dump(mode='json'),data,metadata)

    @api.post("/api/discovery/{opportunity_id}/applications/manual")
    def opportunity_application_manual(opportunity_id: str, data: ManualApplication, request: Request):
        with request.app.state.discovery.db() as db:
            row = db.execute('SELECT job_id,payload FROM opportunities WHERE id=?',(opportunity_id,)).fetchone()
            if not row: raise Missing('Job listing not found.')
            boards = [r[0] for r in db.execute('SELECT DISTINCT board FROM sightings WHERE opportunity_id=?',(opportunity_id,))]
        return request.app.state.applications.confirm_without_resume(row['job_id'] or 'opportunity:'+opportunity_id,json.loads(row['payload']),data,{'boards':boards})

    @api.post("/api/applications/external")
    def application_external(data: ExternalApplication, request: Request):
        return request.app.state.applications.add_external(data)

    @api.post("/api/applications/{application_id}")
    def application_update(application_id: str, data: UpdateApplication, request: Request):
        return request.app.state.applications.update(application_id, data)

    @api.get("/api/applications/{application_id}/resume")
    def application_resume(application_id: str, request: Request):
        return PlainTextResponse(request.app.state.applications.resume(application_id),
            headers={'Content-Disposition': f'attachment; filename="submitted-resume-{application_id}.txt"'})

    @api.get("/api/applications/{application_id}/resume.pdf")
    def application_pdf(application_id: str, request: Request):
        return Response(request.app.state.applications.pdf(application_id), media_type='application/pdf',
            headers={'Content-Disposition':'attachment; filename="submitted-resume.pdf"'})

    @api.get("/api/jobs/{job_id}/tailoring/pdf")
    def tailoring_pdf(job_id: str, revision: str, request: Request):
        from urllib.parse import quote
        tailoring = request.app.state.tailoring
        job = request.app.state.service.review(job_id).job
        pdf = tailoring.export_pdf(job, revision)
        filename = quote(tailoring.pdf_filename(job, revision), safe='')
        return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':"attachment; filename=resume.pdf; filename*=UTF-8''"+filename})

    @api.post("/api/jobs/{job_id}/tailoring/prepare-download")
    def tailoring_prepare_download(job_id: str, data: TailorAction, request: Request):
        return request.app.state.tailoring.prepare_download(request.app.state.service.review(job_id).job, data)

    @api.get("/api/jobs/{job_id}/cover-letter/pdf")
    def cover_letter_pdf(job_id: str, request: Request):
        job = request.app.state.service.store.read(job_id)[0]
        return Response(team_message.cover_pdf(request.app.state.retrieval, job), media_type='application/pdf',
                        headers={'Content-Disposition':'attachment; filename="Cover_letter.pdf"'})

    @api.get("/api/jobs/{job_id}/team-message")
    def team_message_view(job_id: str, request: Request, kind: str = 'introduction'):
        return team_message.view(request.app.state.retrieval, request.app.state.service.store.read(job_id)[0], kind)

    @api.post("/api/jobs/{job_id}/team-message/draft")
    def team_message_draft(job_id: str, data: team_message.DraftInput, request: Request):
        return team_message.generate(request.app.state.tailoring, request.app.state.service.store.read(job_id)[0], data)

    @api.post("/api/jobs/{job_id}/team-message/save")
    def team_message_save(job_id: str, data: team_message.SaveInput, request: Request):
        return team_message.save(request.app.state.retrieval, request.app.state.service.store.read(job_id)[0], data)

    from . import application_answer, answer_library

    @api.get("/api/answer-library")
    def personal_answers(request: Request):
        return application_answer.view(request.app.state.retrieval, answer_library.PERSONAL_LIBRARY)

    @api.post("/api/answer-library/matches")
    def personal_answer_matches(data: answer_library.MatchInput, request: Request):
        def company_of(job_id):
            try: return request.app.state.service.store.read(job_id)[0].company
            except Exception: return ''
        applied = {a['job_id'] for a in request.app.state.applications.list()}
        return answer_library.local_matches(request.app.state.retrieval, data, company_of, applied)

    @api.post("/api/answer-library/draft")
    def personal_answer_draft(data: application_answer.AnswerInput, request: Request):
        return application_answer.generate(request.app.state.tailoring, answer_library.PERSONAL_LIBRARY, data)

    @api.post("/api/answer-library/save")
    def personal_answer_save(data: application_answer.SaveInput, request: Request):
        if not data.text.strip():
            raise RetrievalError('Paste your answer before saving.', 422)
        return application_answer.save(request.app.state.retrieval, answer_library.PERSONAL_LIBRARY, data)

    @api.get("/api/jobs/{job_id}/application-answers")
    def application_answers(job_id: str, request: Request):
        return application_answer.view(request.app.state.retrieval, request.app.state.service.store.read(job_id)[0])

    @api.post("/api/jobs/{job_id}/application-answers/draft")
    def application_answer_draft(job_id: str, data: application_answer.AnswerInput, request: Request):
        return application_answer.generate(request.app.state.tailoring, request.app.state.service.store.read(job_id)[0], data)

    @api.post("/api/jobs/{job_id}/application-answers/matches")
    def application_answer_matches(job_id: str, data: answer_library.MatchInput, request: Request):
        service = request.app.state.service
        def company_of(other):
            try: return service.store.read(other)[0].company
            except Exception: return ''
        applied = {a['job_id'] for a in request.app.state.applications.list()}
        return answer_library.matches(request.app.state.retrieval, service.store.read(job_id)[0], data, company_of, applied)

    @api.post("/api/jobs/{job_id}/application-answers/save")
    def application_answer_save(job_id: str, data: application_answer.SaveInput, request: Request):
        return application_answer.save(request.app.state.retrieval, request.app.state.service.store.read(job_id)[0], data)

    @api.post("/api/tailoring/preferences")
    def tailoring_preferences(data: TailoringPreferences, request: Request):
        return request.app.state.tailoring.save_preferences(data)

    @api.post("/api/tailoring/resume")
    def tailoring_resume(data: TailoringResumeUpload, request: Request):
        return request.app.state.skills.upload_resume(data, prepare=False)

    @api.get("/api/jobs/{job_id}/tailoring")
    def tailoring_view(job_id: str, request: Request):
        return request.app.state.tailoring.view(request.app.state.service.review(job_id).job)

    @api.post("/api/jobs/{job_id}/tailoring")
    def tailoring_generate(job_id: str, data: TailorGenerate, request: Request):
        return request.app.state.tailoring.start(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/tailoring/edit")
    def tailoring_edit(job_id: str, data: TailorEdit, request: Request):
        return request.app.state.tailoring.edit(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/tailoring/format")
    def tailoring_format(job_id: str, data: TailorAction, request: Request):
        return request.app.state.tailoring.format_draft(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/tailoring/proposal")
    def tailoring_proposal(job_id: str, data: TailorProposalDecision, request: Request):
        return request.app.state.tailoring.decide_proposal(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/tailoring/final-review")
    def tailoring_final_review(job_id: str, data: TailorAction, request: Request):
        return request.app.state.tailoring.final_review(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/tailoring/check")
    def tailoring_check(job_id: str, data: TailorAction, request: Request):
        return request.app.state.tailoring.recheck(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/tailoring/approve")
    def tailoring_approve(job_id: str, data: TailorAction, request: Request):
        return request.app.state.tailoring.approve(request.app.state.service.review(job_id).job, data)

    @api.get("/api/jobs/{job_id}/tailoring/export", response_class=PlainTextResponse)
    def tailoring_export(job_id: str, revision: str, request: Request):
        text = request.app.state.tailoring.export(request.app.state.service.review(job_id).job, revision)
        return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="tailored-resume.txt"'})

    @api.get("/api/jobs/{job_id}/analysis")
    def get_analysis(job_id: str, request: Request):
        return request.app.state.analysis.view(request.app.state.service.review(job_id).job)

    @api.post("/api/jobs/{job_id}/analysis/review")
    def accept_analysis(job_id: str, data: ReviewDecision, request: Request):
        manager = request.app.state.analysis
        job = request.app.state.service.review(job_id).job
        manager.workflow.decide(job, data.input_version, data.action)
        if data.action == 'accept':
            PursuitService(manager).automatic(job)
        return manager.view(job)

    @api.get("/api/jobs/{job_id}/pursuit")
    def pursuit_status(job_id: str, request: Request):
        job = request.app.state.service.review(job_id).job
        return PursuitService(request.app.state.analysis).view(job)

    @api.post("/api/jobs/{job_id}/pursuit")
    def assess_pursuit(job_id: str, data: GeneratePursuit, request: Request):
        job = request.app.state.service.review(job_id).job
        return PursuitService(request.app.state.analysis).generate(job, data)

    @api.post("/api/jobs/{job_id}/pursuit/automatic")
    def automatic_pursuit(job_id: str, data: GeneratePursuit, request: Request):
        job = request.app.state.service.review(job_id).job
        return PursuitService(request.app.state.analysis).automatic(job, data)

    @api.post("/api/jobs/{job_id}/pursuit/decision")
    def choose_pursuit(job_id: str, data: ChoosePursuit, request: Request):
        job = request.app.state.service.review(job_id).job
        return PursuitService(request.app.state.analysis).choose(job, data)

    @api.get("/api/jobs/{job_id}/feedback")
    def get_feedback(job_id: str, request: Request):
        job = request.app.state.service.review(job_id).job
        return learning.public(learning.latest(request.app.state.analysis, job))

    @api.post("/api/jobs/{job_id}/feedback")
    def analyze_feedback(job_id: str, data: learning.AnalyzeFeedback, request: Request):
        job = request.app.state.service.review(job_id).job
        return learning.public(learning.analyze(PursuitService(request.app.state.analysis), job, data))

    @api.post("/api/jobs/{job_id}/feedback/resolve")
    def resolve_feedback(job_id: str, data: learning.ResolveFeedback, request: Request):
        job = request.app.state.service.review(job_id).job
        return learning.resolve(PursuitService(request.app.state.analysis), job, data)

    @api.get("/api/jobs/{job_id}/review")
    def get_review_chat(job_id: str, request: Request):
        return review_chat.state(request.app.state.analysis, request.app.state.service.review(job_id).job)

    @api.post("/api/jobs/{job_id}/review/messages")
    def review_message(job_id: str, data: review_chat.ReviewMessage, request: Request):
        return review_chat.send(request.app.state.analysis, request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/review/commit")
    def commit_review(job_id: str, data: review_chat.Commit, request: Request):
        return review_chat.commit(request.app.state.analysis, request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/requirements/{requirement_id}/experience")
    def add_experience(job_id: str, requirement_id: str, data: ExperienceAddition, request: Request):
        return save_addition(request.app.state.analysis, request.app.state.service.review(job_id).job, requirement_id, data)

    @api.post("/api/jobs/{job_id}/experience/retry")
    def retry_experience(job_id: str, data: Consent, request: Request):
        return retry_addition(request.app.state.analysis, request.app.state.service.review(job_id).job)

    @api.post("/api/jobs/{job_id}/analysis")
    def analyze_role(job_id: str, data: AnalyzeInput, request: Request):
        return request.app.state.manual_queue.enqueue(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/requirements/{requirement_id}/evidence")
    def check_evidence(job_id: str, requirement_id: str, data: EvidenceInput, request: Request):
        job = request.app.state.service.review(job_id).job
        manager = request.app.state.analysis
        view = manager.view(job)
        if data.context_version != view['input_version']:
            raise RetrievalError('The evidence changed. Reload before checking.', 409)
        run = view['run']
        if not run or requirement_id not in {r['id'] for r in run['requirements']}:
            raise RetrievalError('Run the whole-role Jev assessment first.', 409)
        if requirement_id not in run['results']:
            raise RetrievalError('Resume the whole-role assessment to complete this requirement.', 409)
        return get_evidence(job_id, requirement_id, request)

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        api.mount("/", StaticFiles(directory=dist, html=True), name="interface")
    return api


app = create_app()
