import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app import application_answer as a
from app.retrieval import RetrievalError
from app.user_workspace import initialize
from app.story_library import COACHING_CLASS

class Engine:
    def __init__(self):self.data={};self.calls=[];self.result={'text':'I built an internal integration prototype.','questions':[],'evidence':[{'source_id':'story','quote':'Built an internal integration prototype.'}]}
    def get(self,key):return self.data.get(key)
    def put(self,key,value):self.data[key]=value
    def paid(self,kind,payload):
        self.calls.append(payload)
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.result)}]}]}

@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'workspace';initialize(root)
    engine=Engine()
    library=SimpleNamespace(root=root,entries=lambda:[{'id':'coach','source_class':COACHING_CLASS,'title':'Positioning Notes'}],describe=lambda *args,**kw:{'text':'Lead with the customer problem.'})
    t=SimpleNamespace(engine=engine,analysis=SimpleNamespace(library=library),baseline=lambda:([{'id':'story','title':'My experience','text':'Built an internal integration prototype.'}],None))
    job=SimpleNamespace(id='one',company='Company',title='Developer PM',description='Own public APIs and SDKs.')
    return engine,t,job

def question(**kw):return a.AnswerInput(id=uuid4(),question='What was broken, what did you change, and how did you verify it?',**kw)

def test_uses_exact_question_full_experience_and_coaching_without_tailoring(setup):
    e,t,j=setup;data=question(limit=100,unit='words',note='The pilot helped three engineers.')
    result=a.generate(t,j,data)
    payload=e.calls[0];body=json.loads(payload['input'])
    assert body['question']==data.question and body['length_limit']=={'maximum':100,'unit':'words'}
    assert body['candidate_sources'][0]['text']=='Built an internal integration prototype.'
    assert body['candidate_sources'][-1]['text']==data.note
    assert body['positioning_guidance'][0]['title']=='Positioning Notes'
    assert 'candidate_fact_rules' in body
    assert a.SKILL.read_text(encoding='utf-8')==payload['instructions']
    assert result['evidence'][0]['title']=='My experience'
    assert set(payload['text']['format']['schema']['required'])=={'text','questions','evidence'}
    a.generate(t,j,data.model_copy(update={'revision':result['revision']}))
    assert len(e.calls)==1

def test_multiple_questions_free_edits_and_conflict_protection(setup):
    e,t,j=setup;first=question();second=question()
    one=a.generate(t,j,first);two=a.generate(t,j,second)
    saved=a.save(e,j,a.SaveInput(**first.model_dump(exclude={'revision'}),revision=one['revision'],text='My own answer'))
    assert len(a.view(e,j)['items'])==2
    assert next(x for x in a.view(e,j)['items'] if x['id']==two['id'])==two
    assert saved['evidence']==[] and len(e.calls)==1
    with pytest.raises(RetrievalError,match='another window'):a.save(e,j,a.SaveInput(**first.model_dump(),text='Stale'))
    assert a.view(e,SimpleNamespace(id='other'))=={'items':[]}

def test_missing_facts_are_saved_as_questions_not_invented_answer(setup):
    e,t,j=setup;e.result={'text':'','questions':['How did you verify the improvement?'],'evidence':[]}
    result=a.generate(t,j,question())
    assert not result['text'] and result['questions']

def test_length_warning_preserves_full_answer_and_counts_characters(setup):
    e,t,j=setup;result=a.generate(t,j,question(limit=3,unit='characters'))
    assert result['limit_exceeded'] and result['text']==e.result['text']

def test_unmatched_excerpt_is_hidden_without_discarding_paid_redraft(setup):
    e,t,j=setup;data=question();saved=a.generate(t,j,data)
    e.result={'text':'I redrafted around the pilot.','questions':[],'evidence':[{'source_id':'story','quote':'Invented ownership of public SDK'}]}
    redraft=a.generate(t,j,data.model_copy(update={'revision':saved['revision'],'note':'Use the pilot story instead.'}))
    assert redraft['text']=='I redrafted around the pilot.' and redraft['note']=='Use the pilot story instead.'
    assert redraft['evidence']==[] and redraft['evidence_dropped']==1
    assert a.view(e,j)['items'][0]==redraft

def test_excerpts_match_despite_typographic_quotes_and_spacing(setup):
    e,t,j=setup
    t.baseline=lambda:([{'id':'story','title':'My experience','text':"Led Example Labs's CAD -\n inspection pilot."}],None)
    e.result={'text':'Answer.','questions':[],'evidence':[{'source_id':'story','quote':'Led Example Labs’s CAD – inspection pilot.'}]}
    result=a.generate(t,j,question())
    assert result['evidence_dropped']==0 and result['evidence'][0]['title']=='My experience'

def test_additional_details_are_sent_as_candidate_directions(setup):
    e,t,j=setup;a.generate(t,j,question(note='Feature the reporting prototype, not the pilot.'))
    body=json.loads(e.calls[0]['input'])
    assert body['candidate_directions']=='Feature the reporting prototype, not the pilot.'
    assert 'candidate_directions' in e.calls[0]['instructions']

def test_empty_question_and_oversized_context_never_sent(setup):
    e,t,j=setup
    with pytest.raises(RetrievalError):a.generate(t,j,question().model_copy(update={'question':' '}))
    j.description='x'*520000
    with pytest.raises(RetrievalError,match='512 KB'):a.generate(t,j,question())
    assert not e.calls

def test_api_saves_multiple_answers_without_resume_or_analysis(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        headers={'X-Local-Review':'1'}
        client.post('/api/setup',headers=headers,json={'target_roles':['PM'],'countries':['US']})
        created=client.post('/api/jobs',headers=headers,json={'request_id':str(uuid4()),'company':'Example','title':'PM','description':'Improve developer onboarding and APIs.'})
        assert created.status_code in (200,201),created.text
        data=created.json()
        job_id=data.get('job',data).get('id')
        assert job_id,data
        route=f'/api/jobs/{job_id}/application-answers'
        assert client.get(route).json()=={'items':[]}
        body=question().model_dump(mode='json')|{'text':'My own saved answer'}
        saved=client.post(route+'/save',headers=headers,json=body)
        assert saved.status_code==200,saved.text
        assert client.get(route).json()['items'][0]['text']==body['text']
        assert client.post(route+'/save',headers=headers,json=body).status_code==409
        assert client.post(route+'/draft',headers=headers,json={**question().model_dump(mode='json'),'limit':0}).status_code==422

def test_completed_deep_analysis_guides_story_choice_without_becoming_evidence(setup):
    e,t,j=setup
    run={'state':'COMPLETE','requirements':[{'id':'R1','interpretation':'Own developer onboarding'},{'id':'R2','interpretation':'Run pricing'}],
         'results':{'a':{'requirement_id':'R1','judgment':{'support':'direct','explanation':'Prototype fixed setup.','evidence':[{'passage_id':'p1','quote':'Built an internal integration prototype.'}]},
                         'passages':[{'passage_id':'p1','source_title':'My experience'}]},
                    'b':{'requirement_id':'R2','judgment':{'support':'unclear','explanation':'','evidence':[]},'passages':[]}}}
    t.analysis.view=lambda job,compact=False:{'run':run}
    a.generate(t,j,question())
    body=json.loads(e.calls[0]['input'])
    assert body['deep_analysis_findings']==[{'requirement':'Own developer onboarding','support':'direct','explanation':'Prototype fixed setup.',
        'stories':[{'title':'My experience','quote':'Built an internal integration prototype.'}]}]
    assert all(s['id']!='deep-analysis' for s in body['candidate_sources'])
    run['state']='RUNNING';e.calls.clear();e.data.clear()
    a.generate(t,j,question())
    assert json.loads(e.calls[0]['input'])['deep_analysis_findings']==[]
