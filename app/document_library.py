"""Save first, then automatically prepare. Failures never discard a saved source."""
import base64
import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from .document_text import extract_document, MAX_FILE_BYTES, EXTRACTOR_VERSION
from .retrieval import RetrievalError, digest
from .story_library import AddStory, EditStory, PrepareStory, COACHING_CLASS


class UploadDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    filename: str = Field(min_length=1, max_length=240)
    content_base64: str = Field(min_length=1, max_length=2_666_668)
    category: Literal["story", "coaching"]
    title: str = Field(min_length=1, max_length=160)
    employer: str = Field(default="Unspecified", min_length=1, max_length=120)
    project: str = Field(default="Unspecified", min_length=1, max_length=160)
    consent_to_send: Literal[True]
    replace_source_id: str | None = Field(default=None, max_length=100)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class SaveAndPrepare(EditStory):
    consent_to_send: Literal[True]


class AddAndPrepare(AddStory):
    consent_to_send: Literal[True]


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_to_send: Literal[True]


def prepare_saved(library, source):
    try:
        library.prepare(source["id"], PrepareStory(expected_revision=source["revision"], consent_to_send=True))
        error = None
    except RetrievalError as exc:
        error = str(exc)
    # Persist the failure so reopening does not hide it or automatically retry.
    entry = library.find(source["id"])
    entry["preparation_error"] = error
    with closing(library.engine.connect()) as db, db:
        db.execute("INSERT OR REPLACE INTO library_sources VALUES (?,?)", (entry["id"], json.dumps(entry)))
    return {"source": library.detail(source["id"]), "preparation_error": error}


def upload_document(library, data: UploadDocument):
    with library.lock:
        try:
            raw = base64.b64decode(data.content_base64, validate=True)
        except ValueError:
            raise RetrievalError("Invalid file encoding.") from None
        if not raw or len(raw) > MAX_FILE_BYTES:
            raise RetrievalError("Upload a nonempty file no larger than 2 MB.")
        sha = hashlib.sha256(raw).hexdigest()
        signature = digest(json.dumps(data.model_dump(mode="json", exclude={"content_base64", "consent_to_send"}), sort_keys=True) + sha)
        entries = library.entries()
        target = library.find(data.replace_source_id) if data.replace_source_id else None
        existing = target if target and target.get("upload_request_id") == str(data.request_id) else next((e for e in entries if e["id"] == str(data.request_id)), None)
        if existing:
            if existing.get("upload_signature") != signature:
                raise RetrievalError("This upload request was already used for another document.", 409)
            return {"source": library.detail(existing["id"]), "preparation_error": existing.get("preparation_error")}
        text = extract_document(data.filename, raw)
        library.validate_text(text)
        source_class = COACHING_CLASS if data.category == "coaching" else "FIRSTHAND_EVIDENCE"
        if target:
            current = library.detail(target["id"])
            if current["revision"] != data.expected_revision or not data.expected_revision:
                raise RetrievalError("Document changed. Refresh before replacing it.", 409)
            if (target["source_class"] == COACHING_CLASS) != (data.category == "coaching"):
                raise RetrievalError("Replacement must keep the same document type.", 409)
        # An unchanged upload is not a new source and does not make more paid calls.
        for entry in ([] if target else entries):
            if entry.get("original_sha256") == sha and entry["source_class"] == source_class:
                current = library.detail(entry["id"])
                if current.get("text") == text:
                    return {"source": current, "preparation_error": entry.get("preparation_error"), "duplicate": True}
        original = library.private_dir / "uploads" / (sha + Path(data.filename).suffix.lower())
        original.parent.mkdir(parents=True, exist_ok=True)
        if not original.exists():
            original.write_bytes(raw)
        if target:
            source = library.edit(target["id"], EditStory(title=data.title, employer=target["employer"],
                                  project=target["project"], text=text, expected_revision=data.expected_revision))
        else:
            source = library.add(AddStory(request_id=data.request_id, title=data.title,
                                         employer=data.employer, project=data.project, text=text, category=data.category))
        entry = library.find(source["id"])
        entry.pop("registered_path", None)
        entry.update(original_filename=Path(data.filename.replace("\\", "/")).name,
                     original_sha256=sha, original_path=str(original.relative_to(library.private_dir)),
                     extractor_version=EXTRACTOR_VERSION, upload_signature=signature, upload_request_id=str(data.request_id))
        with closing(library.engine.connect()) as db, db:
            db.execute("UPDATE library_sources SET payload=? WHERE id=?", (json.dumps(entry), entry["id"]))
        return prepare_saved(library, library.detail(entry["id"]))


def import_coaching(library):
    """Explicit one-time/local refresh action; not a background upload watcher."""
    registry = json.loads((library.root / "knowledge/source_registry.json").read_text(encoding="utf-8"))
    results = []
    for row in registry["sources"]:
        if row.get("class") != COACHING_CLASS:
            continue
        path = (library.root / row["path"]).resolve()
        if not path.is_relative_to(library.root / "knowledge/raw/coaching"):
            raise RetrievalError("Coaching source is outside its allowed folder.")
        raw = path.read_bytes()
        # A changed original imports as a separate, clearly versioned source. Old versions
        # are not implicitly deleted or replaced without a version choice.
        identity = row["path"] + hashlib.sha256(raw).hexdigest()
        result = upload_document(library, UploadDocument(
            request_id=uuid5(NAMESPACE_URL, identity), filename=path.name,
            content_base64=base64.b64encode(raw).decode(), category="coaching",
            title=path.stem.replace("_", " "), employer="User-provided coaching", project="Positioning guidance",
            consent_to_send=True))
        with library.lock:
            entry = library.find(result["source"]["id"])
            entry["registered_path"] = row["path"]
            with closing(library.engine.connect()) as db, db:
                db.execute("UPDATE library_sources SET payload=? WHERE id=?", (json.dumps(entry), entry["id"]))
            result["source"] = library.detail(entry["id"])
        results.append(result)
    return results


def context_pack(root):
    """Deterministic full context for later tailoring; no semantic selection or model call."""
    paths = ["knowledge/profile.json", "knowledge/verified_facts.json", "knowledge/forbidden_claims.json",
             "knowledge/known_conflicts.json", "knowledge/canonical_resume_inventory.json"]
    paths += [str(p.relative_to(root)).replace("\\", "/") for p in sorted((root / "protocols").glob("*.md"))]
    paths += [str(p.relative_to(root)).replace("\\", "/") for p in sorted((root / "knowledge/decisions").glob("*.md"))]
    documents = []
    for name in paths:
        raw = (root / name).read_bytes()
        documents.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode("utf-8")})
    inventory = json.loads((root / "knowledge/canonical_resume_inventory.json").read_text(encoding="utf-8"))
    if inventory.get("source"):
        path = (root / inventory["source"]).resolve()
        if not path.is_relative_to(root / "sources/master"):
            raise RetrievalError("Canonical master path is outside its allowed folder.")
        raw = path.read_bytes()
        documents.insert(0, {"path": inventory["source"], "sha256": hashlib.sha256(raw).hexdigest(),
                             "text": extract_document(path.name, raw), "role": "CANONICAL_MASTER_WITH_RECORDED_CONFLICTS"})
    references = []
    registry = json.loads((root / "knowledge/source_registry.json").read_text(encoding="utf-8"))
    for row in registry["sources"]:
        if row.get("class") != "HISTORICAL_RESUME_NON_AUTHORITATIVE":
            continue
        historical = (root / row["path"]).resolve()
        if not historical.is_relative_to(root / "knowledge/raw/historical-resumes"):
            raise RetrievalError("Historical resume path is outside its allowed folder.")
        content = historical.read_bytes()
        references.append({"path": row["path"], "sha256": hashlib.sha256(content).hexdigest(),
                           "text": extract_document(historical.name, content),
                           "role": row["class"], "restrictions": row.get("restrictions", [])})
    return {"documents": documents, "reference_documents": references, "mode": "FULL_CONTEXT_NOT_SEMANTIC",
            "warning": "Context loader only. Tailoring is not yet connected. Recorded conflicts still block affected claims."}
