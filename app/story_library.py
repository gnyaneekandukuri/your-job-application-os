"""Living source library. File revisions are local; paid preparation is explicit.

All source indexes share the original SQLite usage ledger and query cache.
The canonical resume, approved facts and coaching files are never mutated here.
"""
from .read_snapshot import snapshot_cached
import hashlib
import json
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .retrieval import (BuildInput, SearchInput, RetrievalError, RetrievalService,
                        SOURCE, MODEL, EMBED_MODEL, PROMPT, chunk_story, digest, now, rank_results)
from .relevance import rerank, first_stage, RELEVANCE_VERSION

COACHING_CLASS = "COACHING_ANALYSIS_PENDING_USER_REVIEW"
COACHING_PROMPT = PROMPT + "\nThis is UNREVIEWED COACHING, not firsthand evidence. Describe positioning suggestions as suggestions from the coach. Never convert them into candidate accomplishments or approved claims."


class StoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    employer: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=20, max_length=64000)


class AddStory(StoryDraft):
    request_id: UUID
    category: Literal["story", "coaching"] = "story"


class LibrarySearch(SearchInput):
    category: Literal["story", "coaching"] = "story"


class EditStory(StoryDraft):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class PrepareStory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    consent_to_send: Literal[True]


class StoryLibrary:
    def __init__(self, root: Path, private_dir: Path, provider):
        self.root, self.private_dir, self.provider = root.resolve(), private_dir.resolve(), provider
        self.lock = RLock()
        self.engine = RetrievalService(private_dir / "retrieval.sqlite3", root / SOURCE, provider)
        with closing(self.engine.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS library_sources (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_revisions (
                    source_id TEXT NOT NULL, revision TEXT NOT NULL, saved_at TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(source_id, revision));
            """)

    def entries(self):
        try:
            registry = json.loads((self.root / "knowledge/source_registry.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RetrievalError("Source registry could not be read. No sources were sent.", 409) from None
        entries = {}
        for row in registry["sources"]:
            if row.get("class") not in ("FIRSTHAND_EVIDENCE", "RAW_EVIDENCE") or not row["path"].endswith(".md"):
                continue
            path = (self.root / row["path"]).resolve()
            if not path.is_relative_to(self.root / "knowledge/raw/voice-memos"):
                raise RetrievalError("Story registry contains a path outside the story folder.", 409)
            source_id = digest(row["path"])[:24]
            entries[source_id] = {"id": source_id, "path": row["path"], "title": row.get("title", path.stem),
                                  "employer": row.get("employer", "Unspecified"),
                                  "project": row.get("project", "Unspecified"),
                                  "source_class": row["class"], "origin": "file"}
        with closing(self.engine.connect()) as db:
            rows = db.execute("SELECT id,payload FROM library_sources").fetchall()
        for source_id, payload in rows:
            entry = json.loads(payload)
            if entry["origin"] == "local":
                entries[source_id] = entry
            elif source_id in entries:
                # A registry removal removes the imported source from active search,
                # even if locally edited. Local history is still retained.
                entries[source_id].update({k: entry[k] for k in ("title", "employer", "project")})
                for key in ("preparation_error", "original_filename", "original_sha256", "original_path",
                            "extractor_version", "upload_signature", "upload_request_id"):
                    if key in entry:
                        entries[source_id][key] = entry[key]
        return list(entries.values())

    def service(self, entry):
        base = self.private_dir if entry["origin"] == "local" else self.root
        source = (base / entry["path"]).resolve()
        allowed = self.private_dir / "living-stories" if entry["origin"] == "local" else self.root / "knowledge/raw/voice-memos"
        if not source.is_relative_to(allowed):
            raise RetrievalError("Source path is outside the allowed story folder.", 409)
        prompt = COACHING_PROMPT if entry["source_class"] == COACHING_CLASS else PROMPT
        service = RetrievalService(self.engine.path, source, self.provider, entry["path"], 64000, 100, prompt)
        service.lock = self.lock
        return service

    def find(self, source_id):
        for entry in self.entries():
            if entry["id"] == source_id:
                return entry
        raise RetrievalError("Story not found.", 404)

    def describe(self, entry, include_text=False):
        result = dict(entry)
        result["category"] = "coaching" if entry["source_class"] == COACHING_CLASS else "story"
        result["external_claims_authorized"] = False
        try:
            if entry.get("registered_path"):
                original = (self.root / entry["registered_path"]).resolve()
                registered = json.loads((self.root / "knowledge/source_registry.json").read_text(encoding="utf-8"))["sources"]
                if (not original.is_relative_to(self.root / "knowledge/raw/coaching") or
                    not any(r["path"] == entry["registered_path"] and r.get("class") == COACHING_CLASS for r in registered) or
                    not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != entry.get("original_sha256")):
                    raise RetrievalError("Original coaching file changed or was removed. Re-import it before searching this source.", 409)
            text, sha, chunks, fingerprint = self.service(entry).snapshot()
            index = self.engine.get("index:" + fingerprint)
            from .semantic_context import INDEX_VERSION
            ready = bool(index and index.get('semantic_version') == INDEX_VERSION)
            revision = digest(json.dumps(entry, sort_keys=True) + sha)
            result.update(source_sha256=sha, revision=revision, chunk_count=len(chunks),
                          ready=ready, state="Ready" if ready else "Needs preparation", error=None)
            if include_text:
                result["text"] = text
        except RetrievalError as error:
            result.update(source_sha256=None, revision=None, chunk_count=0, ready=False,
                          state="Unavailable", error=str(error))
        return result

    @snapshot_cached
    def status(self):
        with self.lock:
            sources = [self.describe(e) for e in self.entries()]
            # Ready state is included: finishing one index invalidates old partial results.
            version = digest(RELEVANCE_VERSION + json.dumps([(e["id"], e["revision"], e["ready"]) for e in sources]))
            return {"sources": sources, "source_sha256": version,
                    "ready": any(e["ready"] for e in sources),
                    "ready_count": sum(e["ready"] for e in sources), "source_count": len(sources),
                    "configured": self.provider.configured, "usage": self.engine.usage(),
                    "model": 'jev-1.13.0', "embedding_model": EMBED_MODEL}

    def detail(self, source_id):
        with self.lock:
            return self.describe(self.find(source_id), include_text=True)

    @staticmethod
    def validate_text(text):
        if len(text.encode("utf-8")) > 64000:
            raise RetrievalError("Story is over 64 KB. Split it into separate stories.")
        chunk_story(text, max_chunks=100)

    def add(self, data: AddStory):
        with self.lock:
            self.validate_text(data.text)
            source_id = str(data.request_id)
            content = data.model_dump(mode="json", exclude={"request_id"})
            signature = digest(json.dumps(content, sort_keys=True))
            with closing(self.engine.connect()) as db:
                existing = db.execute("SELECT payload FROM library_sources WHERE id=?", (source_id,)).fetchone()
            if existing:
                if json.loads(existing[0]).get("creation_signature") != signature:
                    raise RetrievalError("This save request was already used for different content.", 409)
                return self.detail(source_id)
            entry = {"id": source_id, "path": f"living-stories/{source_id}.md", "origin": "local",
                     "source_class": COACHING_CLASS if data.category == "coaching" else "FIRSTHAND_EVIDENCE", "creation_signature": signature,
                     **{k: content[k] for k in ("title", "employer", "project")}}
            path = self.private_dir / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text(encoding="utf-8") != data.text:
                raise RetrievalError("An earlier unfinished save exists. Keep it and use a new save request.", 409)
            path.write_bytes(data.text.encode("utf-8"))
            with closing(self.engine.connect()) as db, db:
                db.execute("INSERT INTO library_sources VALUES (?,?)", (source_id, json.dumps(entry)))
            return self.detail(source_id)

    def edit(self, source_id, data: EditStory):
        with self.lock:
            self.validate_text(data.text)
            entry = self.find(source_id)
            previous = self.describe(entry, include_text=True)
            if previous["revision"] != data.expected_revision:
                raise RetrievalError("Story changed since you opened it. Reload before saving.", 409)
            service = self.service(entry)
            # Seed text caches from the existing index before changing the file.
            old_index = self.engine.get("index:" + service.snapshot()[-1])
            if old_index:
                service.seed_passage_embeddings(old_index)
            # Save the exact previous content before replacing it, including metadata.
            with closing(self.engine.connect()) as db, db:
                db.execute("INSERT OR IGNORE INTO library_revisions VALUES (?,?,?,?)",
                           (source_id, previous["revision"], now(), json.dumps(previous)))
            temporary = service.source.with_suffix(".pending")
            temporary.write_bytes(data.text.encode("utf-8"))
            temporary.replace(service.source)
            entry.update({k: getattr(data, k) for k in ("title", "employer", "project")})
            with closing(self.engine.connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO library_sources VALUES (?,?)", (source_id, json.dumps(entry)))
            return self.detail(source_id)

    def prepare(self, source_id, data: PrepareStory):
        with self.lock:
            entry = self.find(source_id)
            current = self.describe(entry, include_text=True)
            if current["error"]:
                raise RetrievalError(current["error"], 409)
            if data.expected_revision != current["revision"]:
                raise RetrievalError("Story changed since preview. Reload and review it before sending.", 409)
            from .semantic_context import prepare_source
            prepare_source(self.service(entry))
            return self.status()

    def search(self, data: SearchInput):
        from .jev_search import search_library
        with self.lock:
            return search_library(self, data)

    def legacy_search(self, data: SearchInput):
        with self.lock:
            before = self.status()
            chunks, used = [], []
            category = getattr(data, "category", "story")
            scoped = [e for e in self.entries() if (e["source_class"] == COACHING_CLASS) == (category == "coaching")]
            for entry in scoped:
                description = self.describe(entry)
                if not description["ready"]:
                    continue
                _, sha, _, fingerprint = self.service(entry).snapshot()
                index = self.engine.get("index:" + fingerprint)
                if not index:
                    continue
                used.append(entry["id"])
                for chunk in index["chunks"]:
                    chunks.append({**chunk, "id": entry["id"] + ":" + chunk["id"],
                                   "source_id": entry["id"], "source_title": entry["title"],
                                   "source_path": entry["path"], "source_sha256": sha,
                                   "source_class": entry["source_class"],
                                   "employer": entry["employer"], "project": entry["project"]})
            if not chunks:
                raise RetrievalError("No current stories are ready. Prepare a story first.", 409)
            key = "query:" + EMBED_MODEL + ":1536:" + digest(data.query)
            vector = self.engine.get(key)
            cached = vector is not None
            if vector is None:
                vector = self.engine.embed([data.query])[0]
                self.engine.put(key, vector)
            checked = rerank(self.engine, chunks, data.query, vector, category)
            # Diagnostic first-stage results contain original text only. Unsafe generated
            # context and contextual vectors never enter the live relevance decision.
            candidate_orders = first_stage(chunks, data.query, vector)
            rankings = {name:[{k:v for k,v in chunks[i].items() if k not in
                               ("context", "vector", "raw_vector", "evidence", "evidence_quotes")}
                              for i in order[:5]] for name,order in candidate_orders.items()}
            rankings["contextual_semantic"] = rankings["semantic"]  # legacy UI key; raw-only now
            if self.status()["source_sha256"] != before["source_sha256"]:
                raise RetrievalError("A story changed during the search. Search again for current results.", 409)
            return {"query": data.query, "source_sha256": before["source_sha256"],
                    "query_cached": cached, "rankings": rankings, **checked,
                    "searched_count": len(used), "source_count": len(scoped), "category": category,
                    "excluded_sources": [e["title"] for e in scoped if e["id"] not in used],
                    "usage": self.engine.usage()}
