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
    assert a.view(j)['stale'] is True

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


def saved_result(monkeypatch):
    e,j,t=fixture(monkeypatch);a=service(e,t)
    a.enqueue(j)
    key=e.get('resume-approach-latest:job')['key']
    result={'state':'COMPLETE',**e.result}
    e.put(key,result)
    a.queue.get_nowait()
    return e,j,a,key,result


def test_changed_master_preserves_result_without_mutating_cache(monkeypatch):
    e,j,a,key,result=saved_result(monkeypatch)
    assert a.view(j)==result
    assert 'stale' not in a.view(j)
    e.put('skills:resume',{'text':'Updated master'})
    assert a.view(j)=={**result,'stale':True,'stale_reason':'checked against previous master'}
    assert e.get(key)==result and 'stale' not in e.get(key)
    assert a.queue.empty() and not e.calls


@pytest.mark.parametrize('result',[
    None, {'state':'QUEUED'}, {'state':'RUNNING'}, {'state':'ERROR','reason':'Failed'},
    {'state':'QUEUED','approach':'USE_MASTER'}, {'state':'RUNNING','approach':'USE_MASTER'},
])
def test_unfinished_old_result_is_not_preserved(monkeypatch,result):
    e,j,a,key,_=saved_result(monkeypatch)
    e.put(key,result)
    e.put('skills:resume',{'text':'Updated master'})
    assert a.view(j)['state']=='NOT_CHECKED'


def test_error_with_saved_approach_is_preserved(monkeypatch):
    e,j,a,key,result=saved_result(monkeypatch)
    e.put(key,{**result,'state':'ERROR'})
    e.put('skills:resume',{'text':'Updated master'})
    assert a.view(j)['state']=='ERROR'
    assert a.view(j)['stale'] is True


def test_bulk_skips_stale_and_explicit_recheck_queues_current_master(monkeypatch,tmp_path):
    from app.main import create_app
    from starlette.requests import Request
    e,j,a,old_key,_=saved_result(monkeypatch)
    old_stamp=e.get('resume-approach-latest:job')['fingerprint']
    e.put('skills:resume',{'text':'Updated master'})
    unchecked=SimpleNamespace(id='unchecked',description='Own a different SDK product')
    e.put('role-analysis-latest:unchecked','run')
    api=create_app(tmp_path)
    api.state.resume_approach=a
    api.state.discovery=SimpleNamespace(saved_job=lambda job_id: j if job_id==j.id else unchecked,
        saved_assessment=lambda job_id:{'state':'COMPLETE'})
    api.state.job_list=SimpleNamespace(assessments=lambda:{'items':[
        {'job_id':job_id,'assessment':{'state':'COMPLETE'}} for job_id in (j.id,unchecked.id)]})
    request=Request({'type':'http','app':api})
    bulk=next(r.endpoint for r in api.routes if r.path=='/api/assessments/resume-approaches')
    explicit=next(r.endpoint for r in api.routes if r.path=='/api/jobs/{job_id}/resume-approach')
    assert bulk(request)=={'queued':1,'unavailable':0}
    assert a.queue.get_nowait()[0].id=='unchecked'
    assert a.queue.empty() and a.view(j)['stale'] is True
    assert explicit(j.id,request)=={'state':'QUEUED'}
    queued_job,new_key,body,new_stamp=a.queue.get_nowait()
    assert queued_job.id==j.id and new_key!=old_key and new_stamp!=old_stamp
    assert body['sources'][0]['text']=='Updated master'
    assert a.view(j)=={'state':'QUEUED'}
    assert not e.calls


def test_batched_views_match_single_views_and_keep_freshness(monkeypatch):
    e,j,t=fixture(monkeypatch);a=service(e,t)
    batches=[]
    def get_many(keys):
        batches.append(list(keys))
        return {k:e.data[k] for k in keys if k in e.data}
    e.get_many=get_many
    assert a.views([j])[j.id]==a.view(j)
    a.enqueue(j)
    key=e.get('resume-approach-latest:job')['key']
    e.put(key,{'state':'COMPLETE','approach':'USE_MASTER'})
    assert a.views([j])[j.id]==a.view(j)
    e.put('skills:resume',{'text':'Changed master'})
    assert a.views([j])[j.id]==a.view(j)
    assert a.views([j])[j.id]['stale']
    assert not e.calls
    assert len(batches)==8
