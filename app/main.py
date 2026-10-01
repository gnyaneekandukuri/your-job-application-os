import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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
from .job_requirements import ExtractRequirements, status as requirements_status, extract as extract_requirements
from .requirement_evidence import EvidenceInput, status as evidence_status, match as match_evidence
from .role_analysis import RoleAnalysis, AnalyzeInput
from .evidence_workflow import ReviewDecision
from .pursuit import PursuitService, Generate as GeneratePursuit, Choose as ChoosePursuit
from .story_library import StoryLibrary, AddStory, EditStory, PrepareStory, LibrarySearch
from .document_library import (UploadDocument, SaveAndPrepare, AddAndPrepare, Consent,
                               upload_document, prepare_saved, import_coaching, context_pack)

from .experience_additions import ExperienceAddition, save_addition, retry as retry_addition

from . import review_conversation as review_chat
from . import feedback_learning as learning
from .discovery import Discovery, Settings as DiscoverySettings, DescriptionInput
from .discovery_keywords import TargetInput, library_sources
from .discovery_preferences import load_preferences
from .user_workspace import initialize, SetupInput, save_setup, setup_view

ROOT = Path(__file__).resolve().parents[1]


def create_app(data_dir: Path | None = None):
    private_dir = data_dir or Path(os.environ.get("JOB_REVIEW_DATA_DIR", ROOT / ".local"))

    workspace = private_dir / "workspace"
    initialize(workspace)

    @asynccontextmanager
    async def lifespan(api):
        store = Store(private_dir / "reviews.sqlite3")
        connection = sqlite3.connect(private_dir / "checkpoints.sqlite3", check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        saver = SqliteSaver(connection)
        saver.setup()
        api.state.service = ReviewService(store, build_graph(saver))
        api.state.retrieval = RetrievalService(private_dir / "retrieval.sqlite3", workspace / SOURCE, OpenAIProvider())
        api.state.library = StoryLibrary(workspace, private_dir, OpenAIProvider())
        api.state.analysis = RoleAnalysis(api.state.retrieval, api.state.library)
        api.state.discovery = Discovery(private_dir / "discovery.sqlite3", api.state.service, api.state.analysis, sources=lambda: library_sources(api.state.library))
        if data_dir is None:
            api.state.discovery.begin_scheduler()
        for job_id in store.ids():
            api.state.service.review(job_id)
        try:
            yield
        finally:
            api.state.discovery.close()
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
            limit = 2_800_000 if request.url.path == "/api/library/upload" else 300000
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
        return request.app.state.discovery.view()

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

    @api.post("/api/discovery/settings")
    def discovery_settings(data: DiscoverySettings, request: Request):
        return request.app.state.discovery.configure(data)

    @api.post("/api/discovery/{opportunity_id}/description/fetch")
    def discovery_fetch_description(opportunity_id: str, request: Request):
        return request.app.state.discovery.fetch_description(opportunity_id)

    @api.post("/api/discovery/{opportunity_id}/description")
    def discovery_save_description(opportunity_id: str, data: DescriptionInput, request: Request):
        return request.app.state.discovery.save_description(opportunity_id, data)

    @api.post("/api/discovery/{opportunity_id}/assess")
    def discovery_assess(opportunity_id: str, request: Request):
        return request.app.state.discovery.import_role(opportunity_id)

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
        return upload_document(request.app.state.library, data)

    @api.post("/api/library/add-and-prepare")
    def add_and_prepare(data: AddAndPrepare, request: Request):
        library = request.app.state.library
        with library.lock:
            source = library.add(AddStory(**data.model_dump(exclude={"consent_to_send"})))
            return prepare_saved(library, source)

    @api.post("/api/library/sources/{source_id}/save-and-prepare")
    def save_and_prepare(source_id: str, data: SaveAndPrepare, request: Request):
        library = request.app.state.library
        with library.lock:
            source = library.edit(source_id, EditStory(**data.model_dump(exclude={"consent_to_send"})))
            return prepare_saved(library, source)

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
        return requirements_status(request.app.state.retrieval, job)

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
        return evidence_status(request.app.state.retrieval, request.app.state.library, job, requirement_id)

    @api.get("/api/jobs/{job_id}/analysis")
    def get_analysis(job_id: str, request: Request):
        return request.app.state.analysis.view(request.app.state.service.review(job_id).job)

    @api.post("/api/jobs/{job_id}/analysis/review")
    def accept_analysis(job_id: str, data: ReviewDecision, request: Request):
        manager = request.app.state.analysis
        job = request.app.state.service.review(job_id).job
        manager.workflow.decide(job, data.input_version, data.action)
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
        return request.app.state.analysis.start(request.app.state.service.review(job_id).job, data)

    @api.post("/api/jobs/{job_id}/requirements/{requirement_id}/evidence")
    def check_evidence(job_id: str, requirement_id: str, data: EvidenceInput, request: Request):
        job = request.app.state.service.review(job_id).job
        return match_evidence(request.app.state.retrieval, request.app.state.library, job, requirement_id, data)

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        api.mount("/", StaticFiles(directory=dist, html=True), name="interface")
    return api


app = create_app()
