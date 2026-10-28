import json
from types import SimpleNamespace
import pytest
from app import team_message as m
from app.retrieval import RetrievalError

class Engine:
    def __init__(self): self.data,self.calls={},[]
    def get(self,k): return self.data.get(k)
    def put(self,k,v): self.data[k]=v
    def paid(self,kind,payload):
        self.calls.append(payload)
        result={'text':'I built a data assistant and would like to discuss this role.'}
        if payload['text']['format']['name']=='cover_letter':
            result['needs']=[{'jd_quote':json.loads(payload['input'])['jd'][:16],'evidence':'Built a data assistant'}]*2
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}

def setup():
    e=Engine();j=SimpleNamespace(id='job',company='Example',title='PM',description='Build data products')
    context={'sources':[{'text':'Built a data assistant','title':'Career story'}],
             'positioning_guidance':[{'title':'Positioning notes','text':'Example positioning theme'}],
             'candidate_fact_rules':{'facts':{'revenue':'pilot opportunity'}}}
    t=SimpleNamespace(engine=e,resume_options=lambda job:[{'id':'draft:1','text':'Saved resume'}],
                      context=lambda job,resume,**kwargs:('',context,'version'))
    return e,j,t,context

def test_draft_uses_stories_positioning_facts_and_caches():
    e,j,t,c=setup()
    v=m.generate(t,j,m.DraftInput(note='Interested in enterprise data'))
    body=json.loads(e.calls[0]['input'])
    assert body['sources']==c['sources']
    assert body['positioning_guidance']==c['positioning_guidance']
    assert body['fact_rules']==c['candidate_fact_rules']
    assert body['personal_note']=='Interested in enterprise data'
    m.generate(t,j,m.DraftInput(note=v['note'],revision=v['revision']))
    assert len(e.calls)==1

def test_edit_is_free_persistent_and_protects_other_window():
    e,j,t,c=setup()
    v=m.save(e,j,m.SaveInput(text='My own introduction',note='My goals'))
    assert m.view(e,j)['text']=='My own introduction' and not e.calls
    with pytest.raises(RetrievalError):m.save(e,j,m.SaveInput(text='Stale overwrite'))
    assert m.view(e,j)==v

def test_changed_guidance_invalidates_and_oversize_never_sent():
    e,j,t,c=setup();v=m.generate(t,j,m.DraftInput())
    c['positioning_guidance'][0]['text']='Updated positioning'
    v=m.generate(t,j,m.DraftInput(revision=v['revision']))
    assert len(e.calls)==2

    j.description='x'*520000
    with pytest.raises(RetrievalError):m.generate(t,j,m.DraftInput(revision=v['revision']))
    assert len(e.calls)==2

def test_new_instructions_preserve_saved_edits_until_explicit_redraft(monkeypatch):
    e,j,t,c=setup()
    first=m.generate(t,j,m.DraftInput())
    edited=m.save(e,j,m.SaveInput(text='My personal edited introduction',revision=first['revision']))
    monkeypatch.setattr(m,'PROMPT',m.PROMPT+'\nUpdated writing guidance.')
    assert m.view(e,j)['text']==edited['text']
    m.generate(t,j,m.DraftInput(revision=edited['revision']))
    assert len(e.calls)==2
    assert e.calls[-1]['instructions']==m.PROMPT

def test_company_answer_is_separate_and_question_changes_cache():
    e,j,t,c=setup()
    intro=m.save(e,j,m.SaveInput(text='My saved YC intro'))
    answer=m.generate(t,j,m.DraftInput(kind='why_company',question='What excites you about Example?'))
    assert e.calls[-1]['instructions']==m.COMPANY_PROMPT
    assert json.loads(e.calls[-1]['input'])['application_question']=='What excites you about Example?'
    assert m.view(e,j)==intro
    assert m.view(e,j,'why_company')==answer
    answer=m.generate(t,j,m.DraftInput(kind='why_company',question=answer['question'],revision=answer['revision']))
    assert len(e.calls)==1
    answer=m.generate(t,j,m.DraftInput(kind='why_company',question='Why this role? In 50 words.',revision=answer['revision']))
    assert len(e.calls)==2
    saved=m.save(e,j,m.SaveInput(kind='why_company',question=answer['question'],revision=answer['revision'],text='My edited answer'))
    assert m.view(e,j,'why_company')==saved and len(e.calls)==2
    assert m.view(e,j)==intro


@pytest.mark.parametrize('kind',['introduction','why_company','cover_letter'])
def test_large_message_context_preserved_in_full(kind):
    e,j,t,c=setup()
    j.description='Responsibilities. '*1800
    c['sources'][0]['text']='Candidate experience. '*1800
    c['positioning_guidance'][0]['text']='Candidate positioning. '*2000
    m.generate(t,j,m.DraftInput(kind=kind))
    payload=e.calls[0]
    assert len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))>100000
    body=json.loads(payload['input'])
    assert body['jd']==j.description
    assert body['sources']==c['sources']
    assert body['positioning_guidance']==c['positioning_guidance']
    assert len(e.calls)==1


def test_cover_letter_saved_edits_exported_without_calls():
    import io
    from pypdf import PdfReader
    e,j,t,c=setup()
    original=m.save(e,j,m.SaveInput(text='Keep my introduction'))
    answer=m.generate(t,j,m.DraftInput(kind='cover_letter',question='Under 300 words'))
    assert m.COVER_SKILL.read_text(encoding='utf-8') in e.calls[-1]['instructions']
    assert e.calls[-1]['instructions'].endswith(m.COVER_PROMPT)
    assert json.loads(e.calls[-1]['input'])['application_question']=='Under 300 words'
    m.save(e,j,m.SaveInput(kind='cover_letter',revision=answer['revision'],text='Dear Example team,\n\nMy edited letter with a specific product example.\n\nBest,\nAlex'))
    pdf=m.cover_pdf(e,j)
    assert 'My edited letter' in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert len(e.calls)==1 and m.view(e,j)==original


def test_missing_cover_skill_fails_before_paid_request(tmp_path,monkeypatch):
    e,j,t,c=setup()
    monkeypatch.setattr(m,'COVER_SKILL',tmp_path/'missing.md')
    with pytest.raises(RetrievalError,match='skill could not be loaded'):
        m.generate(t,j,m.DraftInput(kind='cover_letter'))
    assert not e.calls


def test_cover_letter_is_planned_from_jd_needs_and_rejects_invented_quotes():
    e,j,t,c=setup()
    m.generate(t,j,m.DraftInput(kind='cover_letter'))
    assert e.calls[-1]['text']['format']['name']=='cover_letter'
    assert 'most central to this role' in e.calls[-1]['instructions']
    paid=e.paid
    def invented(kind,payload):
        response=paid(kind,payload)
        data=json.loads(response['output'][0]['content'][0]['text']);data['needs'][0]['jd_quote']='Own pricing for a global portfolio'
        response['output'][0]['content'][0]['text']=json.dumps(data);return response
    e.paid=invented
    with pytest.raises(RetrievalError,match='not in this JD'):
        m.generate(t,j,m.DraftInput(kind='cover_letter',note='new',revision=m.view(e,j,'cover_letter')['revision']))
