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


def profile_for(passages=None):
    from app.discovery_screening import candidate_profile
    return candidate_profile({'skills':[{'key':'ai','name':'AI agents','aliases':[]}]},
                             {'target_roles':['AI Product Manager']}, passages or [
        {'id':'source','source_title':'My experience','text':'I built an agent evaluation workflow with customer feedback.'}])


def test_screen_embeds_once_sends_only_retrieved_text_and_routes_confident_high(tmp_path,monkeypatch):
    provider=Embeddings();engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',provider)
    calls=[]
    class Classifier:
        def __init__(self,engine): pass
        def evaluate(self,job,stage,state,questions):
            calls.append(state)
            assert 'skills' not in state
            assert len(state['retrieved_experience'])<=3
            assert all('similarity' not in p for p in state['retrieved_experience'])
            return {'priority':{'choice':'high','confidence':.9 if state['job']['title']=='Strong' else .7}}
    monkeypatch.setattr('app.discovery_screening.JevClient',Classifier)
    profile=profile_for()
    profile['skills'] += [{'id':str(i),'name':'Skill '+str(i)} for i in range(500)]
    items=[{'id':t,'title':t,'description':'Own AI agents and evaluate customer workflows. '*8,
            'location_match':{'status':'match'}} for t in ('Strong','Uncertain')]
    assert screen(engine,items,profile,Event())['classified']==2
    annotate(engine,items,profile)
    assert [i['keyword_match']['priority'] for i in items]==['high','high']
    assert [Discovery.auto_eligible(i) for i in items]==[True,True]
    before=list(provider.inputs)
    assert screen(engine,items,profile,Event())['cached']==2
    annotate(engine,items,profile)
    assert len(calls)==2 and provider.inputs==before
    assert before.count(profile['_passages'][0]['text'])==1
    profile['preferences']['target_roles']=['Designer']
    annotate(engine,items,profile)
    assert all(i['keyword_match']['label']=='Not screened' for i in items)
    assert len(calls)==2


def test_priority_uncertainty_and_needs_information_never_auto_route():
    from app.discovery_screening import priority_for
    assert priority_for({'choice':'high','confidence':.8})=='high'
    assert priority_for({'choice':'high','confidence':.7})=='high'
    assert priority_for({'choice':'high','confidence':.69})=='worth_checking'
    assert priority_for({'choice':'high','confidence':1,'provider_inconsistent':True})=='worth_checking'
    assert priority_for({'choice':'needs_information','confidence':1})=='worth_checking'
    assert priority_for({'choice':'low','confidence':.9})=='low'
    assert priority_for({'choice':'low','confidence':.3})=='worth_checking'


def test_missing_foreign_and_empty_context_do_not_call_apis(tmp_path):
    provider=Embeddings();engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',provider)
    missing={'id':'missing','title':'AI PM','description':''}
    foreign={'id':'foreign','title':'PM','description':'Responsibilities '*30,'location_match':{'status':'outside'}}
    profile=profile_for()
    counts=screen(engine,[missing,foreign],profile,Event())
    assert counts['needs_description']==1 and not provider.inputs
    annotate(engine,[missing],profile)
    assert missing['keyword_match']['label']=='Needs information'
    profile['current']=False
    counts=screen(engine,[{**foreign,'location_match':{'status':'match'}}],profile,Event())
    assert counts['needs_profile']==1 and not provider.inputs


def test_context_changes_invalidate_cached_priority_without_network():
    from app.discovery_screening import inputs
    item={'title':'PM','description':'JD'}
    first=inputs(item,profile_for())[1]
    changed=profile_for([{'id':'source','source_title':'My experience','text':'I changed my saved account.'}])
    assert inputs(item,changed)[1]!=first
    hidden=profile_for();hidden['skills']=[]
    assert inputs(item,hidden)[1]!=first


def test_oversized_jd_fails_before_any_embedding(tmp_path):
    provider=Embeddings();engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',provider)
    item={'id':'large','title':'PM','description':'x'*65000}
    # Unreadable text is routed to the candidate before any paid embedding or screening call.
    counts=screen(engine,[item],profile_for(),Event())
    assert counts['needs_description']==1 and provider.inputs==[]
    annotate(engine,[item],profile_for())
    assert item['keyword_match']['priority']=='worth_checking' and item['keyword_match']['label']=='Needs information'


def test_error_circuit_breaks_without_repeated_provider_calls(tmp_path,monkeypatch):
    from app.retrieval import RetrievalError
    provider=Embeddings();engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',provider)
    calls=[]
    class Classifier:
        def __init__(self,engine): pass
        def evaluate(self,*args):calls.append(1);raise RetrievalError('TypeSafe unavailable')
    monkeypatch.setattr('app.discovery_screening.JevClient',Classifier)
    items=[{'id':str(i),'title':str(i),'description':'Responsibilities '*30} for i in range(2)]
    assert screen(engine,items,profile_for(),Event())['errors']==1
    assert len(calls)==1
    assert screen(engine,items[:1],profile_for(),Event())['cached']==1
    assert len(calls)==1


def test_resume_only_context_can_match_without_generated_skills(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.model_provider import OpenAIProvider
    monkeypatch.setattr(OpenAIProvider,'request',lambda *a: (_ for _ in ()).throw(AssertionError('No API call expected on read')))
    with TestClient(create_app(tmp_path)) as c:
        library=c.app.state.library
        path=library.root/'sources/master/current.md';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('I owned the AI platform roadmap and customer discovery.')
        (library.root/'knowledge/canonical_resume_inventory.json').write_text(json.dumps({'source':'sources/master/current.md'}))
        profile=c.app.state.discovery.screening_profile({'target_roles':['PM']})
        assert profile['current']
        assert any('resume' in p['id'] for p in profile['_passages'])


def test_old_answers_are_reused_with_new_cutoff_without_provider_calls(tmp_path):
    from app.discovery_screening import inputs, LEGACY_VERSION
    provider=Embeddings();engine=RetrievalService(tmp_path/'r.sqlite3',tmp_path/'unused',provider)
    profile=profile_for()
    item={'id':'saved','title':'AI PM','description':'Specific responsibilities '*30}
    key=inputs(item,profile,LEGACY_VERSION)[1]
    engine.put(key, {'state':'classified','priority':'worth_checking','reason':'Old cutoff',
                     'answer':{'choice':'high','confidence':.75},'matches':[]})
    assert screen(engine,[item],profile,Event())['cached']==1
    annotate(engine,[item],profile)
    assert item['keyword_match']['priority']=='high'
    assert provider.inputs==[]
    profile['preferences']['target_roles']=['Designer']
    annotate(engine,[item],profile)
    assert item['keyword_match']['label']=='Not screened'
