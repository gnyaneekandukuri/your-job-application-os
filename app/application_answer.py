"""Editable application answers, independent of resume tailoring and submission."""
import json
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import UUID, uuid4
from pydantic import Field
from .job_requirements import Strict
from .retrieval import MODEL, RetrievalError, digest, now
from .tailoring import parse_response, response_schema, REQUEST_BYTE_LIMIT
from .cost_tracking import usage_scope
from .user_workspace import control_documents

SKILL = Path(__file__).resolve().parents[1] / 'skills/application-answer/SKILL.md'
LOCK = RLock()

class AnswerInput(Strict):
    id: UUID
    revision: str = ''
    question: str = Field(min_length=1, max_length=6000)
    note: str = Field(default='', max_length=6000)
    limit: int | None = Field(default=None, ge=1, le=10000)
    unit: Literal['words','characters'] = 'words'

class SaveInput(AnswerInput):
    text: str = Field(max_length=16000)

class Evidence(Strict):
    source_id: str
    quote: str = Field(min_length=1, max_length=1500)

class Answer(Strict):
    text: str = Field(max_length=16000)
    questions: list[str] = Field(max_length=4)
    evidence: list[Evidence] = Field(max_length=6)

TYPOGRAPHY = str.maketrans({'‘':"'",'’':"'",'“':'"','”':'"','–':'-','—':'-',' ':' '})

def loose(text): return ' '.join(text.translate(TYPOGRAPHY).replace('…','...').split())

def key(job): return 'application-answers:'+job.id

def view(engine,job): return engine.get(key(job)) or {'items':[]}

def current(engine,job,data):
    old=next((r for r in view(engine,job)['items'] if r['id']==str(data.id)),None)
    if (old['revision'] if old else '') != data.revision:
        raise RetrievalError('This answer changed in another window. Reopen it before saving.',409)
    if not data.question.strip(): raise RetrievalError('Paste the application question first.',422)
    return old

def persist(engine,job,data,answer):
    row={**data.model_dump(mode='json'),**answer,'revision':str(uuid4()),'updated_at':now()}
    length=len(row['text']) if data.unit=='characters' else len(row['text'].split())
    row['limit_exceeded']=bool(data.limit and length>data.limit)
    rows=[r for r in view(engine,job)['items'] if r['id']!=row['id']]
    engine.put(key(job),{'items':[row]+rows})
    return row

def save(engine,job,data):
    with LOCK:
        old=current(engine,job,data)
        unchanged=old and all(old.get(k)==getattr(data,k) for k in ('text','question','note'))
        return persist(engine,job,data,{'text':data.text,'questions':old.get('questions',[]) if unchanged else [],'evidence':old.get('evidence',[]) if unchanged else [],
            'evidence_dropped':old.get('evidence_dropped',0) if unchanged else 0})

def context(tailoring,job,data):
    sources,_=tailoring.baseline()
    sources=[{k:s.get(k,'') for k in ('id','title','text','employer','project')} for s in sources if s.get('text','').strip()]
    if not sources and not data.note.strip():
        raise RetrievalError('Add your experience in Your context or provide details in the note first.',422)
    if data.note.strip():sources.append({'id':'personal-note','title':'Your additional details','text':data.note})
    library=tailoring.analysis.library
    from .story_library import COACHING_CLASS
    guidance=[]
    for entry in library.entries():
        if entry['source_class']!=COACHING_CLASS:continue
        document=library.describe(entry,include_text=True)
        if document.get('error'):raise RetrievalError('A positioning document could not be read. Update it in Your context.',409)
        guidance.append({'title':entry['title'],'text':document['text']})
    docs=control_documents(library.root)
    return {'question':data.question,'length_limit':{'maximum':data.limit,'unit':data.unit},
        'candidate_directions':data.note,
        'job':{'company':job.company,'title':job.title,'description':job.description},
        'candidate_sources':sources,'positioning_guidance':guidance,
        'candidate_fact_rules':{k:json.loads(docs[k]) for k in ('facts','forbidden','conflicts')}}

def generate(tailoring,job,data):
    engine=tailoring.engine
    with LOCK:
        current(engine,job,data)
        body=context(tailoring,job,data)
        try:policy=SKILL.read_text(encoding='utf-8')
        except OSError:raise RetrievalError('Application-answer instructions are unavailable. Nothing was sent.',503) from None
        if not policy.strip():raise RetrievalError('Application-answer instructions are empty. Nothing was sent.',503)
        payload={'model':MODEL,'store':False,'reasoning':{'effort':'none'},'max_output_tokens':5000,
            'instructions':policy,'input':json.dumps(body,ensure_ascii=False),
            'text':{'format':{'type':'json_schema','name':'application_answer','strict':True,'schema':response_schema(Answer)}}}
        if len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))>REQUEST_BYTE_LIMIT:
            raise RetrievalError('Application-answer context exceeds the 512 KB local request limit. Nothing was sent; your context is unchanged.',422)
        cache='application-answer-result:'+digest(json.dumps(payload,sort_keys=True))
        result=engine.get(cache)
        if result is None:
            with usage_scope(job.id,'application_question'):
                result=parse_response(engine.paid('tailoring',payload),Answer).model_dump()
            by_id={s['id']:s for s in body['candidate_sources']}
            if not result['text'].strip() and not result['questions']:raise RetrievalError('No answer or clarification was returned. No automatic retry was made.',502)
            # Excerpts are shown for review only; hide unmatched ones instead of discarding a paid answer.
            evidence=[]
            for ref in result['evidence']:
                source=by_id.get(ref['source_id'])
                if source and loose(ref['quote']) in loose(source['text']):evidence.append({**ref,'title':source['title']})
            result['evidence_dropped']=len(result['evidence'])-len(evidence)
            result['evidence']=evidence
            engine.put(cache,result)
        return persist(engine,job,data,result)
