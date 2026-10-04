import json
from threading import Event
from types import SimpleNamespace

from app.retrieval import RetrievalService
from app.semantic_context import prepare_source, embedding_key
from app.jev_assessment import candidate_excerpts
from app.discovery_screening import screen, annotate
from app.discovery import Discovery


class Embeddings:
    configured = True
    def __init__(self): self.inputs = []
    def request(self, endpoint, payload):
        assert endpoint == 'embeddings'
        self.inputs.extend(payload['input'])
        return {'data': [{'index': i, 'embedding': [1.] + [0.]*1535} for i in range(len(payload['input']))],
                'usage': {'prompt_tokens': len(payload['input'])*20}}


def test_context_embeddings_incremental_across_documents_and_edits(tmp_path):
    provider = Embeddings()
    source = tmp_path/'story.md'
    source.write_text('I interviewed users and changed our roadmap.\n\nI measured adoption after launch.')
    engine = RetrievalService(tmp_path/'r.sqlite3', source, provider)
    prepare_source(engine)
    original = list(provider.inputs)
    assert len(original) == 2
    prepare_source(engine)
    assert provider.inputs == original
    source.write_text('I interviewed users and changed our roadmap.\n\nI measured retention after launch.')
    prepare_source(engine)
    assert provider.inputs[len(original):] == ['I measured retention after launch.']
    other = tmp_path/'another.md'; other.write_text(source.read_text())
    prepare_source(RetrievalService(engine.path,other,provider))
    assert len(provider.inputs) == 3  # Exact text is reusable across source IDs too.


def test_semantic_search_finds_differently_worded_evidence_before_lexical_distractors(tmp_path):
    provider=Embeddings(); engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',provider)
    passages=[{'id':str(i),'text':f'Customer discovery is in our corporate brochure number {i}.','source_title':'Brochure'} for i in range(20)]
    passages.append({'id':'experience','text':'I interviewed buyers, identified unmet needs, and changed the roadmap.','source_title':'My work'})
    for p in passages:
        vector = [1.,0.] if p['id']=='experience' else [0.,1.]
        engine.put(embedding_key(p['text']),vector+[0.]*1534)
    first=candidate_excerpts(passages,'Lead customer discovery.',engine=engine)
    assert first[0]['passage_id']=='experience'
    assert len(first)==12
    candidate_excerpts(passages,'Lead customer discovery.',engine=engine)
    assert provider.inputs==['Lead customer discovery.']
    assert all('raw_vector' not in u for u in first)  # No vectors sent as Jev evidence.


def test_first_pass_never_embeds_and_only_high_routes_automatically(tmp_path,monkeypatch):
    class NoEmbeddings:
        configured=True
        def request(self,*args): raise AssertionError('First pass called OpenAI')
    engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',NoEmbeddings())
    calls=[]
    class Classifier:
        def __init__(self,engine): pass
        def evaluate(self,job,stage,state,questions):
            calls.append(state)
            label={'Aligned':'strong','Adjacent':'plausible','Other':'mismatch'}[state['job']['title']]
            return {'fit':{'choice':label,'confidence':1.}, 'role':{'choice':'mismatch' if label=='mismatch' else 'aligned','confidence':1.}, **{'skill_'+str(n):{'choice':'direct','confidence':1.} for n in range(len(state['skills']))}}
    monkeypatch.setattr('app.discovery_screening.JevClient',Classifier)
    profile={'current':True,'target':{'roles':['Product Manager']},'skills':[{'id':'research','name':'Research','aliases':[],'examples':[]}]}
    items=[{'id':title,'title':title,'description':'Original product responsibilities. '*10,'location_match':{'status':'match'},
            'keyword_match':{'priority':'worth_checking','ranking':{}}} for title in ('Aligned','Adjacent','Other')]
    screen(engine,items,profile,Event());annotate(engine,items,profile)
    assert [i['keyword_match']['priority'] for i in items]==['high','worth_checking','low']
    assert [Discovery.auto_eligible(i) for i in items]==[True,False,False]
    screen(engine,items,profile,Event());annotate(engine,items,profile)
    assert len(calls)==3
    profile['target']['roles']=['Designer']
    annotate(engine,items,profile)
    assert all(i['keyword_match']['priority']=='worth_checking' for i in items)
    assert len(calls)==3  # Changing targets/refreshing never dispatches paid work.

def test_priority_requires_semantic_support_and_role_alignment():
    from app.discovery_screening import priority_for
    strong={'choice':'strong','confidence':.95}
    aligned={'choice':'aligned','confidence':.95}
    assert priority_for(strong,aligned)=='high'
    assert priority_for({**strong,'confidence':.4},aligned)=='worth_checking'
    assert priority_for(strong,{'choice':'mismatch','confidence':.95})=='low'
    assert priority_for(strong,{**aligned,'provider_inconsistent':True})=='worth_checking'


def test_profile_edits_invalidate_matching_and_hidden_skills_are_excluded():
    from app.discovery_screening import candidate_profile, inputs
    view={'current':True,'skills':[{'key':'ai','name':'AI agents','sources':[]},{'key':'hidden','name':'Hidden','hidden':True}]}
    profile=candidate_profile(view,{'target_roles':['PM']})
    assert len(profile['skills'])==1
    first=inputs({'title':'PM','description':'JD'},profile)[1]
    view['skills'][0]['name']='Data platforms'
    assert inputs({'title':'PM','description':'JD'},candidate_profile(view,{'target_roles':['PM']}))[1]!=first


def test_current_resume_is_in_fast_profile_without_embeddings(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.discovery_keywords import library_sources
    from app.model_provider import OpenAIProvider
    monkeypatch.setattr(OpenAIProvider,'request',lambda *a: (_ for _ in ()).throw(AssertionError('No API call expected')))
    with TestClient(create_app(tmp_path)) as c:
        library=c.app.state.library
        path=library.root/'sources/master/current.md';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('I owned the AI platform roadmap and customer discovery.')
        (library.root/'knowledge/canonical_resume_inventory.json').write_text(json.dumps({'source':'sources/master/current.md'}))
        sources=library_sources(library)
        assert sources[0]['id']=='current-resume'
        assert sources[0]['text']==path.read_text()

def test_all_skills_participate_and_incomplete_profiles_do_not_call_provider(tmp_path, monkeypatch):
    from app.discovery_screening import inputs
    engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',Embeddings())
    seen=[]
    class Classifier:
        def __init__(self, engine): pass
        def evaluate(self, job, stage, state, questions):
            seen.extend(s['id'] for s in state['skills'])
            return {'fit':{'choice':'strong','confidence':1.},'role':{'choice':'aligned','confidence':1.},
                    **{'skill_'+str(n):{'choice':'direct','confidence':1.} for n in range(len(state['skills']))}}
    monkeypatch.setattr('app.discovery_screening.JevClient',Classifier)
    profile={'current':False,'target':{'roles':['PM']},'skills':[{'id':str(i),'name':'Skill '+str(i)} for i in range(208)]}
    items=[{'id':'job','title':'PM','description':'Responsibilities '*30}]
    assert screen(engine,items,profile,Event())['needs_profile']==1
    assert seen==[]
    profile['current']=True
    assert screen(engine,items,profile,Event())['classified']==1
    assert seen==[str(i) for i in range(208)]
    assert screen(engine,items,profile,Event())['cached']==1
    assert len(seen)==208
    profile['current']=False
    annotate(engine,items,profile)
    assert items[0]['keyword_match']['label']=='Not screened'

def test_request_packing_bounds_full_payload_without_dropping_skills():
    import json
    import pytest
    from app.discovery_screening import request_batches, questions_for, POLICY
    from app.retrieval import RetrievalError
    state={'job':{'description':'Responsibilities '*100},'target_preferences':{'roles':['PM']},
           'skills':[{'id':str(i),'name':'Skill '+str(i),'examples':[{'quote':'Evidence '*100}]} for i in range(208)]}
    batches=list(request_batches(state))
    assert len(batches)>2
    assert [s['id'] for b in batches for s in b]==[str(i) for i in range(208)]
    for b in batches:
        body={'state':{**state,'skills':b},'questions':questions_for(b)}
        assert len(json.dumps(body,ensure_ascii=False).encode())<=POLICY['screen_request_bytes']
    with pytest.raises(RetrievalError,match='too large'):
        list(request_batches({**state,'job':{'description':'x'*50000}}))


def test_explanation_uncertainty_does_not_suppress_priority(tmp_path,monkeypatch):
    engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',Embeddings())
    class Classifier:
        def __init__(self, engine): pass
        def evaluate(self, job, stage, state, questions):
            assert not any(k.startswith('match') for k in questions)
            return {'role':{'choice':'aligned','confidence':.95},'fit':{'choice':'strong','confidence':.95},
                    'skill_0':{'choice':'direct','confidence':.4},'skill_1':{'choice':'transferable','confidence':.4}}
    monkeypatch.setattr('app.discovery_screening.JevClient',Classifier)
    profile={'current':True,'target':{'roles':['PM']},'skills':[{'id':'a','name':'AI agents'},{'id':'b','name':'Evaluation'}]}
    items=[{'id':'job','title':'PM','description':'Responsibilities '*30}]
    screen(engine,items,profile,Event());annotate(engine,items,profile)
    assert items[0]['keyword_match']['priority']=='high'
    assert items[0]['keyword_match']['experience']==[]
