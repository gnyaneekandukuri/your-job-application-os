import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.jev_client import JevClient, MODEL, choice
from app.jev_requirements import CATEGORIES, extract, spans
from app.job_requirements import ExtractRequirements, source_hash
from app.main import create_app
from app.model_provider import OpenAIProvider
from app.retrieval import RetrievalService, RetrievalError


def answer_body(body, uncertain=False):
    answers = {}
    for name,q in body['questions'].items():
        options = q['criteria']
        if set(options) == set(CATEGORIES):
            text = next(b['text'] for b in body['state']['blocks'] if b['id']==name)
            selected = 'context' if text.endswith(':') else 'preference' if 'preferred' in text else 'requirement' if 'required' in text else 'responsibility'
        elif 'supports' in options:
            selected = 'supports'
        elif 'demonstrated' in options:
            selected = 'demonstrated'
        elif 'strong' in options:
            selected = 'strong'
        elif 'similar_work' in options:
            selected = 'similar_work'
        elif 'clear' in options:
            selected = 'clear'
        elif name.startswith('constraint_'):
            selected = 'unknown'
        elif name.startswith('priority_'):
            selected = 'not_established'
            if name=='priority_role_content':
                selected = next(b['id'] for b in body['state']['blocks'] if 'Own ' in b['text'])
        elif name.startswith('core_'):
            selected = 'core'
        else:
            selected = next(iter(options))
        probabilities = {k:float(k==selected) for k in options}
        confidence = 1.
        if uncertain and name=='scope':
            probabilities = {k:(.6 if k==selected else .4/(len(options)-1)) for k in options}
            confidence = .46
        answers[name] = {'type':'choice','choice':selected,'confidence':confidence,'probabilities':probabilities}
    return {'model':MODEL,'answers':answers,'usage':{'input_tokens':100,'output_tokens':10}}


def engine_at(path):
    return RetrievalService(path/'retrieval.sqlite3',path/'unused.md',OpenAIProvider(allow_generation=False,allow_embeddings=False))


def test_jev_cache_and_failed_usage_are_accounted(tmp_path):
    engine = engine_at(tmp_path); calls=[]
    def post(body):
        calls.append(body)
        return answer_body(body)
    client = JevClient(engine,post)
    q = {'q':choice('Pick a class.',{'yes':'Yes','no':'No'})}
    client.evaluate('job','test',{'text':'example'},q)
    client.evaluate('job','test',{'text':'example'},q)
    assert len(calls)==1
    with engine.connect() as db:
        assert db.execute('SELECT stage,input_tokens,state FROM jev_assessment_calls').fetchone()==('test',100,'RECEIVED')
    def bad(body):
        result=answer_body(body)
        result['answers']['q']['probabilities']['yes']=float('nan')
        return result
    failed=JevClient(engine,bad)
    with pytest.raises(RetrievalError): failed.evaluate('job','bad',{'text':'new'},q)
    with pytest.raises(RetrievalError,match='already failed'): failed.evaluate('job','bad',{'text':'new'},q)
    with engine.connect() as db:
        rows=db.execute('SELECT cost,state FROM jev_assessment_calls ORDER BY id').fetchall()
        assert len(rows)==2 and rows[-1][0] is not None and rows[-1][1]=='FAILED_OR_UNKNOWN'


def test_requirement_classification_preserves_or_negation_and_caches_across_jobs(tmp_path):
    engine=engine_at(tmp_path); calls=[]
    client=JevClient(engine,lambda body:(calls.append(body) or answer_body(body)))
    jd='Qualifications:\nPython or SQL is preferred, not required.'
    for job_id in ('a','b'):
        job=SimpleNamespace(id=job_id,description=jd)
        result=extract(engine,job,ExtractRequirements(consent_to_send=True,source_sha256=source_hash(job)),client)['result']
        assert result['job_id']==job_id
        assert result['items'][0]['quote']=='Python or SQL is preferred, not required.'
        assert result['items'][0]['category']=='preference'
    assert len(calls)==1
    with pytest.raises(RetrievalError): spans('x'*1500)


def test_unknown_jev_outcome_has_no_automatic_retry(tmp_path):
    calls=[]
    def fail(body):
        calls.append(1)
        raise RuntimeError('SECRET PROVIDER ERROR')
    engine=engine_at(tmp_path); client=JevClient(engine,fail)
    args=('j','test',{}, {'q':choice('Classify',{'a':'A','b':'B'})})
    for _ in range(2):
        with pytest.raises(RetrievalError) as exc: client.evaluate(*args)
        assert 'SECRET' not in str(exc.value)
    assert len(calls)==1
    with engine.connect() as db:
        assert db.execute('SELECT cost FROM jev_assessment_calls').fetchone()[0] is None


@pytest.mark.parametrize('uncertain', [False,True])
def test_complete_workflow_uses_embeddings_but_no_openai_generation(tmp_path,monkeypatch,uncertain):
    embedding_calls=[]
    def embeddings_only(self,endpoint,payload):
        assert endpoint=='embeddings', 'Generative OpenAI call reached assessment'
        embedding_calls.append(payload['input'])
        return {'model':payload['model'],'data':[{'index':i,'embedding':[1.]+[0.]*1535} for i in range(len(payload['input']))], 'usage':{'prompt_tokens':100}}
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setattr(OpenAIProvider,'request',embeddings_only)
    original=JevClient.__init__
    def init(self,engine,post=None): original(self,engine,lambda body:answer_body(body,uncertain))
    monkeypatch.setattr(JevClient,'__init__',init)
    headers={'X-Local-Review':'1'}
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup',headers=headers,json={'target_roles':['Product Manager'],'countries':['US']})
        story=client.post('/api/library/sources',headers=headers,json={'request_id':str(uuid4()),'title':'Launch','employer':'Example','project':'AI platform',
            'text':'I owned product discovery and the AI platform roadmap. I prioritized evaluation workflows and led the product launch.'}).json()
        prepared=client.post(f"/api/library/sources/{story['id']}/prepare",headers=headers,json={'expected_revision':story['revision'],'consent_to_send':True})
        assert prepared.status_code==200,prepared.text
        saved=client.post('/api/jobs',headers=headers,json={'request_id':str(uuid4()),'title':'Product Manager','company':'Example',
            'description':'Responsibilities:\nOwn product discovery and the AI platform roadmap.'}).json()
        job_id=saved['job']['id']; path=f'/api/jobs/{job_id}/analysis'
        version=client.get(path).json()['input_version']
        result=client.post(path,headers=headers,json={'input_version':version,'consent_to_send':True})
        assert result.status_code==200,result.text
        client.app.state.analysis.worker.join(15)
        result=client.get(path).json()
        assert result['run']['state']=='COMPLETE', result['run']['error']
        assert result['run']['model']==MODEL
        assert result['score']['percent']==100
        assert result['score']['provisional'] is uncertain
        evidence=next(iter(result['run']['results'].values()))
        assert evidence['passages'][0]['quote'] in story['text']
        assert evidence['judgment']['assessment']['review_required'] is uncertain
        pursuit=client.get(f'/api/jobs/{job_id}/pursuit').json()
        assert pursuit['result'],pursuit
        assert pursuit['result']['recommendation']==('CASUAL' if uncertain else 'ATTACK_HARD')
        usage=client.get('/api/usage').json()['calls']
        assert usage and embedding_calls
        assert all(c['provider']=='Jev' or c['kind']=='embedding' for c in usage)
        count=len(usage)
        with client.app.state.discovery.db() as db:
            db.execute('INSERT INTO opportunities VALUES(?,?,?,?,?,?,?)',
                       ('example',json.dumps({'description':'A newer listing'}),'now','now',job_id,'old','COMPLETE'))
        listed=client.get('/api/assessments').json()['items'][0]['assessment']
        assert listed['state']=='COMPLETE' and listed['listing_changed']
        assert listed['score']==100  # A newer board snapshot must not hide the saved report.
        client.post(path,headers=headers,json={'input_version':version,'consent_to_send':True})
        assert len(client.get('/api/usage').json()['calls'])==count
        if uncertain:
            reviewed=client.post(path+'/review',headers=headers,json={'input_version':version,'action':'accept'})
            assert reviewed.status_code==200,reviewed.text
            assert reviewed.json()['score']['provisional'] is False
            assert reviewed.json()['score']['percent']==100  # Confirmation never adds score credit.
            assert len(client.get('/api/usage').json()['calls'])==count  # Cached typed judgments only.


def test_app_provider_blocks_both_openai_endpoints_before_network():
    provider=OpenAIProvider(allow_generation=False,allow_embeddings=False)
    for endpoint in ('responses','embeddings'):
        with pytest.raises(RetrievalError,match='disabled'):
            provider.request(endpoint,{'input':'not sent'})

@pytest.mark.parametrize('mode', ['irrelevant','tentative_relevance','tentative_clear','conflict'])
def test_evidence_abstention_and_uncertainty_are_distinct(tmp_path,mode):
    from app.jev_assessment import assess
    from app.job_requirements import SavedRequirement
    text='I owned the platform roadmap and prioritized the launch.'
    p={'id':'p1','text':text,'source_title':'Example story','source_path':'sources/example.md',
       'source_sha256':'a'*64,'source_class':'FIRSTHAND_EVIDENCE','employer':'Example',
       'heading':'Platform','start_line':1,'end_line':1}
    req=SavedRequirement(id='r1',category='responsibility',quote='Own the platform roadmap.',interpretation='Own the platform roadmap.',explanation='Original duty.')
    def post(body):
        result=answer_body(body)
        for name,a in result['answers'].items():
            if name.startswith('u') and mode in ('irrelevant','tentative_relevance'):
                selected='irrelevant' if mode=='irrelevant' else 'supports'
                a.update(choice=selected,confidence=1. if mode=='irrelevant' else .4,
                         probabilities={k:float(k==selected) for k in a['probabilities']})
            if name=='conflict' and mode in ('tentative_clear','conflict'):
                a.update(choice='clear' if mode=='tentative_clear' else 'unresolved',
                         confidence=.4 if mode=='tentative_clear' else 1.,
                         probabilities={'clear':.7 if mode=='tentative_clear' else 0.,'unresolved':.3 if mode=='tentative_clear' else 1.})
        return result
    engine=engine_at(tmp_path)
    from app.semantic_context import embedding_key
    for value in (text,req.quote): engine.put(embedding_key(value),[1.]+[0.]*1535)
    result=assess(JevClient(engine,post),'job',[req],[p],{'facts':{'facts':[]}})[0][1]
    if mode in ('irrelevant','conflict'):
        assert result.assessment.dimensions.actions.anchor=='insufficient_evidence'
        if mode=='irrelevant': assert not result.evidence
    else:
        assert result.assessment.dimensions.actions.anchor=='demonstrated'
        assert result.assessment.review_required
        assert result.evidence[0].quote==text

def test_scan_high_priority_handoff_completes_semantic_assessment(tmp_path,monkeypatch):
    from app.discovery import Settings
    from app.retrieval import now
    import threading
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    calls=[]
    def embed(self,endpoint,payload):
        assert endpoint=='embeddings'
        calls.extend(payload['input'])
        return {'data':[{'index':i,'embedding':[1.]+[0.]*1535} for i in range(len(payload['input']))],'usage':{'prompt_tokens':100}}
    monkeypatch.setattr(OpenAIProvider,'request',embed)
    original=JevClient.__init__
    monkeypatch.setattr(JevClient,'__init__',lambda self,engine,post=None:original(self,engine,answer_body))
    headers={'X-Local-Review':'1'}
    with TestClient(create_app(tmp_path)) as c:
        c.post('/api/setup',headers=headers,json={'target_roles':['Product Manager'],'countries':['US']})
        story=c.post('/api/library/sources',headers=headers,json={'request_id':str(uuid4()),'title':'Product','employer':'Example','project':'Platform',
            'text':'I owned product management for our AI platform and prioritized the roadmap with customer discovery.'}).json()
        c.post(f"/api/library/sources/{story['id']}/prepare",headers=headers,json={'expected_revision':story['revision'],'consent_to_send':True})
        d=c.app.state.discovery
        d.configure(Settings(enabled=False,auto_assess=True))
        rows=[{'source_id':'high','title':'Product Manager','company':'Example','url':'https://example.com/pm','location':'New York, USA',
            'description':'Own product discovery and the AI platform roadmap. '*5,'posted_at':now()}]
        d.collector=lambda board:(rows,'Complete for test',[])
        monkeypatch.setattr(d.jev,'run',lambda items,stop:{'state':'complete'})
        # The saved candidate-aware Jev screening, not a forced priority flag,
        # determines whether this enters the automatic worker.
        d.scan(['yc'])
        if d.analysis.worker:d.analysis.worker.join(15)
        jobs=c.app.state.service.store.ids()
        assert len(jobs)==1
        report=c.get(f'/api/jobs/{jobs[0]}/analysis').json()
        assert report['run']['state']=='COMPLETE',report['run']
        assert calls  # Context and requirement embeddings, no generative calls.
        count=len(calls)
        d.scan(['yc'])
        assert len(calls)==count  # A later scan does not re-embed or re-assess it.

def test_near_tied_provider_disagreement_is_review_only_and_material_disagreement_fails():
    from app.jev_client import validate_answers,JevValidationError
    questions={'q':choice('Classify',{'supports':'Direct','transferable':'Related','unclear':'Unknown','irrelevant':'None'})}
    response={'model':MODEL,'answers':{'q':{'type':'choice','choice':'supports','confidence':.29,
        'probabilities':{'supports':.46,'transferable':.47,'unclear':.04,'irrelevant':.03}}}}
    answer=validate_answers(response,questions)['q']
    assert answer['provider_inconsistent'] and answer['confidence']==0
    assert response['answers']['q']['confidence']==.29  # Raw provider reply preserved.
    response['answers']['q']['probabilities']={'supports':.1,'transferable':.9,'unclear':0,'irrelevant':0}
    with pytest.raises(JevValidationError):validate_answers(response,questions)
