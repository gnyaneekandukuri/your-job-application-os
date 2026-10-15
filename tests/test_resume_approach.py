import json
from queue import Queue
from threading import RLock
from types import SimpleNamespace
import pytest
from app import resume_approach as mod
from app.retrieval import RetrievalError

class Engine:
    def __init__(self):
        self.data={};self.calls=[]
        self.result={'approach':'USE_MASTER','reason':'The master already demonstrates API ownership and adoption outcomes.','changes':[],'experience_gaps':['No demonstrated payments domain experience.']}
    def get(self,key):return self.data.get(key)
    def put(self,key,value):self.data[key]=value
    def paid(self,kind,payload):
        self.calls.append(payload)
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.result)}]}]}

def fixture(monkeypatch):
    e=Engine();j=SimpleNamespace(id='job',description='Own public APIs and SDK adoption')
    e.put('skills:resume',{'text':'Owned developer APIs; reduced integration time 30%.'})
    e.put('role-analysis-latest:job','run')
    e.put('run',{'state':'COMPLETE','results':{'r':{'judgment':{'evidence':[{'passage_id':'p'}]},'passages':[
        {'passage_id':'p','source_class':'FIRSTHAND_EVIDENCE','source_path':'story','source_title':'Developer story','quote':'Built an SDK','text':'Built an SDK'},
        {'passage_id':'unused','source_class':'COACHING_GUIDANCE','text':'SECRET marketing claims'}]}}})
    t=SimpleNamespace(engine=e,skills=SimpleNamespace(engine=e),analysis=SimpleNamespace(engine=e,library=SimpleNamespace(root='unused')))
    t.baseline=lambda:([{'path':'story','text':'Built an SDK'}],e.get('skills:resume'))
    monkeypatch.setattr('app.user_workspace.control_documents',lambda root:{k:'{}' for k in ('facts','forbidden','conflicts')})
    return e,j,t

def service(e,t):
    a=object.__new__(mod.ResumeApproach);a.engine=e;a.tailoring=t;a.lock=RLock();a.queue=Queue()
    return a

def test_inputs_and_cache_read_have_no_paid_calls(monkeypatch):
    e,j,t=fixture(monkeypatch);a=service(e,t)
    assert a.view(j)['state']=='NOT_CHECKED'
    key,body=mod.inputs(t,j)
    assert 'SECRET' not in json.dumps(body)
    assert body['sources'][0]['text']==e.get('skills:resume')['text']
    a.enqueue(j);a.enqueue(j)
    assert a.queue.qsize()==1 and not e.calls
    e.put(key,mod.evaluate(e,j,key,body))
    assert a.view(j)['approach']=='USE_MASTER'
    a.enqueue(j);assert len(e.calls)==1 and a.queue.qsize()==1
    e.put('skills:resume',{'text':'New master'})
    assert a.view(j)['state']=='NOT_CHECKED'

def test_failed_request_requires_explicit_retry(monkeypatch):
    e,j,t=fixture(monkeypatch);a=service(e,t);key,_=mod.inputs(t,j)
    e.put(key,{'state':'ERROR','reason':'Provider outcome unknown'})
    a.enqueue(j);assert a.queue.empty()
    a.enqueue(j,retry=True);assert a.queue.qsize()==1

@pytest.mark.parametrize('approach,ids',[('SMALL_EDITS',['master']),('TAILOR',['evidence:1'])])
def test_supported_specific_recommendations(monkeypatch,approach,ids):
    e,j,t=fixture(monkeypatch);key,body=mod.inputs(t,j)
    e.result.update(approach=approach,changes=[{'job_need':'SDK ownership','resume_point':'Developer platform experience','action':'Emphasize the SDK delivery work','evidence_ids':ids}])
    assert mod.evaluate(e,j,key,body)['approach']==approach
    assert len(e.calls)==1

@pytest.mark.parametrize('approach,ids',[('SMALL_EDITS',['evidence:1']),('TAILOR',['master']),('TAILOR',['invented'])])
def test_unsubstantiated_advice_rejected(monkeypatch,approach,ids):
    e,j,t=fixture(monkeypatch);key,body=mod.inputs(t,j)
    e.result.update(approach=approach,changes=[{'job_need':'SDK ownership','resume_point':'Experience','action':'Add achievement','evidence_ids':ids}])
    with pytest.raises(RetrievalError):mod.evaluate(e,j,key,body)

def test_empty_or_oversize_never_sent(monkeypatch):
    e,j,t=fixture(monkeypatch);e.put('skills:resume',{'text':''})
    with pytest.raises(RetrievalError):mod.inputs(t,j)
    with pytest.raises(RetrievalError):mod.evaluate(e,j,'key',{'job_description':'x'*130000})
    assert not e.calls
