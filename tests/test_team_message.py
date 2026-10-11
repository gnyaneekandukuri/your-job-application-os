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
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'text':'I built a data assistant and would like to discuss this role.'})}]}]}

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
    j.description='x'*110000
    with pytest.raises(RetrievalError):m.generate(t,j,m.DraftInput(revision=v['revision']))
    assert len(e.calls)==2
