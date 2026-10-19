"""Reuse your saved answers to general application questions across jobs.

Company-specific questions never match. Matching compares question meaning with
embeddings (no generation call); the candidate always chooses whether to reuse.
"""
import re
from contextlib import closing
from pydantic import Field
from .job_requirements import Strict
from .retrieval import EMBED_MODEL, digest
from .cost_tracking import usage_scope

THRESHOLD = 0.55
LIMIT = 3
PREFIX = 'application-answers:'
SPECIFIC = re.compile(
    r"\bwhy\b[^?.!]{0,60}\b(us|join|joining|work|working|here|company|role|team|position|opportunity|interested|apply|applying)\b"
    r"|\bintro(?:duce|duction|ductions)?\b"
    r"|\b(?:this|our|the)\s+(?:role|position|team|company|opportunity|job)\b", re.I)
SUFFIX = re.compile(r"[,.]?\s*\b(inc|llc|ltd|corp|corporation|co|company|technologies|labs|ai|hq)\b\.?$", re.I)


class MatchInput(Strict):
    question: str = Field(min_length=1, max_length=6000)
    limit: int | None = Field(default=None, ge=1, le=10000)
    unit: str = Field(default='words', pattern='^(words|characters)$')


def company_names(company):
    name = ' '.join((company or '').casefold().split())
    names = {name, SUFFIX.sub('', name).strip(), name.replace(' ', ''), name.split('.')[0]}
    return {n for n in names if len(n) >= 3}


def specific(question, company=''):
    """True when the answer depends on this employer or role."""
    text = ' '.join(question.casefold().split())
    return bool(SPECIFIC.search(text) or any(n in text or n in text.replace(' ', '') for n in company_names(company)))


def reusable(row, company=''):
    return row['reusable'] if row.get('reusable') is not None else not specific(row.get('question', ''), company)


def length(text, unit):
    return len(text) if unit == 'characters' else len(text.split())


def entries(engine, company_of):
    """Latest saved answer per distinct question, across every job."""
    with closing(engine.connect()) as db:
        rows = db.execute('SELECT key,payload FROM cache WHERE key LIKE ?', (PREFIX + '%',)).fetchall()
    import json
    latest = {}
    for key, payload in rows:
        job_id = key[len(PREFIX):]
        for row in json.loads(payload).get('items', []):
            if not row.get('text', '').strip() or not row.get('question', '').strip(): continue
            company = row.get('company') or company_of(job_id)
            if not reusable(row, company): continue
            norm = ' '.join(re.findall(r'[a-z0-9]+', row['question'].casefold()))
            entry = {**row, 'job_id': job_id, 'company': company}
            if norm not in latest or entry.get('updated_at', '') > latest[norm].get('updated_at', ''):
                latest[norm] = entry
    return list(latest.values())


def vectors(engine, texts):
    """One embedding per distinct question text, cached so a saved question is embedded once."""
    keys = ['answer-question-embedding:' + EMBED_MODEL + ':' + digest(t) for t in texts]
    found = engine.get_many(keys)
    missing = [t for t, k in zip(texts, keys) if found.get(k) is None]
    if missing:
        for text, vector in zip(missing, engine.embed(missing)):
            k = 'answer-question-embedding:' + EMBED_MODEL + ':' + digest(text)
            engine.put(k, vector); found[k] = vector
    return [found[k] for k in keys]


def stories_changed_at(engine):
    try:
        with closing(engine.connect()) as db:
            return db.execute('SELECT MAX(saved_at) FROM library_revisions').fetchone()[0] or ''
    except Exception:
        return ''


def matches(engine, job, data, company_of):
    if specific(data.question, job.company):
        return {'specific': True, 'matches': []}
    candidates = [e for e in entries(engine, company_of) if e['job_id'] != job.id]
    if not candidates:
        return {'specific': False, 'matches': []}
    with usage_scope(job.id, 'application_answer_match'):
        found = vectors(engine, [data.question] + [e['question'] for e in candidates])
    query, changed = found[0], stories_changed_at(engine)
    scored = sorted(((sum(a * b for a, b in zip(query, v)), e) for v, e in zip(found[1:], candidates)),
                    key=lambda pair: -pair[0])
    result = []
    for score, e in scored[:LIMIT]:
        if score < THRESHOLD: break
        size = length(e['text'], data.unit)
        result.append({'job_id': e['job_id'], 'answer_id': e['id'], 'company': e['company'], 'question': e['question'],
                       'text': e['text'], 'evidence': e.get('evidence', []), 'score': round(score, 2),
                       'updated_at': e.get('updated_at', ''), 'length': size, 'unit': data.unit,
                       'over_limit': bool(data.limit and size > data.limit),
                       'stories_changed': bool(changed and e.get('updated_at') and changed > e['updated_at'])})
    return {'specific': False, 'matches': result}
