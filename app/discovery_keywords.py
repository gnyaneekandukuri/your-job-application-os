"""Persistent free keyword profiles and listing matches; independent of embeddings."""
import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2"
from .discovery_priority import best_job_hit, context_hit, rank


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def phrase_pattern(term):
    words = re.findall(r"[a-z0-9]+", term.lower())
    return r"(?<!\w)" + r"[\s_/-]+".join(map(re.escape, words)) + r"(?!\w)" if words else r"(?!)"


def occurrence(text, terms, context=False):
    for number, line in enumerate(text.splitlines(), 1):
        if context:
            return context_hit(text, terms, lambda value, aliases: occurrence(value, aliases))
        for term in terms:
            match = re.search(phrase_pattern(term), line, re.I)
            if match:
                start = max(0, match.start()-100)
                return {"line": number, "excerpt": line[start:match.end()+160].strip(), "matched_text": match.group()}
    return None


class TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    roles: list[str] = Field(min_length=1, max_length=30)
    keywords: list[str] = Field(max_length=60)
    expected_version: str


class KeywordIndex:
    def __init__(self, path, sources=lambda: []):
        self.path, self.sources, self.lock = path, sources, RLock()
        with closing(self.connect()) as db, db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS keyword_sources (id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT);
              CREATE TABLE IF NOT EXISTS keyword_jobs (id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT);
              CREATE TABLE IF NOT EXISTS keyword_matches (id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT);
              CREATE TABLE IF NOT EXISTS keyword_target (id INTEGER PRIMARY KEY, payload TEXT);
              CREATE TABLE IF NOT EXISTS keyword_target_history (id INTEGER PRIMARY KEY, saved_at TEXT, payload TEXT);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    @staticmethod
    def read(db, table, key, fingerprint):
        row = db.execute(f"SELECT payload FROM {table} WHERE id=? AND fingerprint=?", (key, fingerprint)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def save(db, table, key, fingerprint, payload):
        db.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?)", (key, fingerprint, json.dumps(payload)))

    def target(self, db, preferences):
        row = db.execute("SELECT payload FROM keyword_target WHERE id=1").fetchone()
        value = json.loads(row[0]) if row else {"roles": preferences["target_roles"], "keywords": preferences["preferred_focus_terms"]}
        return {**value, "version": digest(value)}

    def update_target(self, data, preferences):
        from .store import Conflict
        from .retrieval import RetrievalError
        value = {key: list(dict.fromkeys(s.strip() for s in getattr(data,key) if s.strip())) for key in ("roles", "keywords")}
        if not value["roles"] or any(len(s)>120 or not re.search(r"[a-zA-Z0-9]",s) for terms in value.values() for s in terms):
            raise RetrievalError("Enter at least one role and keep each phrase under 120 characters.", 422)
        with self.lock, closing(self.connect()) as db, db:
            if self.target(db,preferences)["version"] != data.expected_version:
                raise Conflict("Targets changed. Reload before saving.")
            payload=json.dumps(value)
            db.execute("INSERT OR REPLACE INTO keyword_target VALUES(1,?)", (payload,))
            db.execute("INSERT INTO keyword_target_history(saved_at,payload) VALUES(?,?)", (datetime.now(timezone.utc).isoformat(),payload))

    def profile(self, db, vocabulary, preferences):
        experience = {}
        fingerprints = []
        for source in self.sources():
            fingerprint=digest([VERSION,vocabulary,source])
            keywords=self.read(db,"keyword_sources",source["id"],fingerprint)
            if keywords is None:
                keywords=[]
                for term, aliases in vocabulary["concepts"].items():
                    hit=occurrence(source["text"],aliases,context=True)
                    if hit:
                        keywords.append({"term":term,"source":{**{k:source[k] for k in ("id","title","employer","project","path")},**hit}})
                self.save(db,"keyword_sources",source["id"],fingerprint,keywords)
            fingerprints.append([source["id"],fingerprint])
            for keyword in keywords:
                experience.setdefault(keyword["term"],[]).append(keyword["source"])
        return {"experience":[{"term":term,"sources":sources} for term,sources in sorted(experience.items())],
                "experience_version":digest(sorted(fingerprints)),"target":self.target(db,preferences)}

    def enrich(self, items, preferences):
        vocabulary=json.loads((Path(self.path).parent/"workspace/criteria/discovery-keywords.json").read_text(encoding="utf-8"))
        policy=json.loads((Path(self.path).parent/"workspace/criteria/discovery-priority.json").read_text(encoding="utf-8"))
        with self.lock, closing(self.connect()) as db, db:
            profile=self.profile(db,vocabulary,preferences)
            target=profile["target"]
            concepts=vocabulary["concepts"]
            for item in items:
                job_hash=digest([VERSION,vocabulary,policy,item.get("title",""),item.get("description","")])
                hits=self.read(db,"keyword_jobs",item["id"],job_hash)
                if hits is None:
                    hits={}
                    for term,aliases in concepts.items():
                        hit=best_job_hit(item,aliases,policy,occurrence)
                        if hit:
                            hits[term]=hit
                    self.save(db,"keyword_jobs",item["id"],job_hash,hits)
                fingerprint=digest([job_hash,profile["experience_version"],target,preferences])
                result=self.read(db,"keyword_matches",item["id"],fingerprint)
                if result is None:
                    experience=[{**entry,"listing":hits[entry["term"]]} for entry in profile["experience"] if entry["term"] in hits]
                    targets=[]
                    for term in [*target["roles"],*target["keywords"]]:
                        aliases=[term]
                        for label, alternatives in concepts.items():
                            if term.lower() in [s.lower() for s in [label,*alternatives]]:
                                aliases=alternatives;break
                        hit=best_job_hit(item,aliases,policy,occurrence)
                        if hit:
                            targets.append({"term":term,**hit})
                    from .discovery_preferences import matches
                    ranked=rank(experience,targets,target,item.get("title",""),concepts,preferences,policy,occurrence,matches)
                    contribution={c['term']:c['points'] for c in ranked['ranking']['contributions']}
                    experience.sort(key=lambda entry:-contribution[entry['term']])
                    result={**ranked,
                            "experience":experience,"target":targets,
                            "experience_version":profile["experience_version"],"target_version":target["version"],"job_version":job_hash}
                    self.save(db,"keyword_matches",item["id"],fingerprint,result)
                item["keyword_match"]=result
            return profile


def library_sources(library):
    # Reads saved stories only, including unprepared uploads. No embedding calls.
    with library.lock:
        result=[]
        for entry in library.entries():
            if entry["source_class"] not in ("FIRSTHAND_EVIDENCE","RAW_EVIDENCE"):
                continue
            path=library.service(entry).source
            if path.is_file():
                result.append({**{k:entry[k] for k in ("id","title","employer","project","path")},"text":path.read_text(encoding="utf-8")})
        return result
