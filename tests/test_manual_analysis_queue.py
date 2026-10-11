import json
import sqlite3
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.manual_analysis_queue import ManualAnalysisQueue, PREFIX
from app.role_analysis import AnalyzeInput

class Engine:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY,payload TEXT)')
    def connect(self):return sqlite3.connect(self.path)
    def put(self,key,value):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(key,json.dumps(value)))
    def get(self,key):
        with self.connect() as db:row=db.execute('SELECT payload FROM cache WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

@pytest.fixture
def setup(tmp_path):
    engine=Engine(tmp_path/'queue.db')
    analysis=SimpleNamespace(engine=engine,lock=RLock(),worker=Mock(),view=Mock(return_value={'input_version':'a'*64,'ready':True,'run':None}),start=Mock(return_value={'run':{'state':'RUNNING'}}))
    analysis.worker.is_alive.return_value=True
    job=SimpleNamespace(id='one',company='Disney',title='PM',source_url='https://example.org/job')
    queue=ManualAnalysisQueue(analysis,lambda _:job)
    queue.start=Mock()
    return queue,analysis,job

def request(queue,job):return queue.enqueue(job,AnalyzeInput(consent_to_send=True,input_version='a'*64))

def test_busy_request_is_durable_idempotent_and_visible(setup):
    q,a,j=setup
    request(q,j);request(q,j);q.step()
    assert len(q.entries())==1
    assert q.display_items()[0]['company']=='Disney'
    assert q.display_items()[0]['assessment']['state']=='QUEUED'
    a.start.assert_not_called()
    restarted=ManualAnalysisQueue(a,lambda _:j)
    assert restarted.pending()
    a.worker.is_alive.return_value=False
    restarted.step()
    a.start.assert_called_once()
    assert restarted.entries()[0]['state']=='RUNNING'

def test_fifo_and_failure_does_not_retry(setup):
    q,a,j=setup;request(q,j)
    second=SimpleNamespace(**{**vars(j),'id':'two'});request(q,second)
    q.job_loader=lambda id:j if id=='one' else second
    a.worker.is_alive.return_value=False
    q.step();assert a.start.call_args.args[0].id=='one'
    a.engine.put('role-analysis:one:'+('a'*64),{'state':'ERROR','error':'Provider failed'})
    q.reconcile();q.step()
    assert a.start.call_count==2 and a.start.call_args.args[0].id=='two'
    assert a.engine.get(PREFIX+'one')['state']=='NEEDS_ATTENTION'

def test_changed_inputs_are_not_sent(setup):
    q,a,j=setup;request(q,j)
    a.worker.is_alive.return_value=False
    a.view.return_value['input_version']='b'*64
    q.step();a.start.assert_not_called()
    assert q.entries()[0]['state']=='NEEDS_ATTENTION'

def test_restart_does_not_repeat_interrupted_paid_request(setup):
    q,a,j=setup;request(q,j);a.worker.is_alive.return_value=False;q.step()
    restarted=ManualAnalysisQueue(a,lambda _:j);restarted.step()
    assert restarted.entries()[0]['state']=='NEEDS_ATTENTION'
    assert a.start.call_count==1

def test_finished_request_reconciles_after_restart(setup):
    q,a,j=setup;request(q,j);a.worker.is_alive.return_value=False;q.step()
    a.engine.put('role-analysis:one:'+('a'*64),{'state':'COMPLETE'})
    restarted=ManualAnalysisQueue(a,lambda _:j)
    assert restarted.entries()[0]['state']=='COMPLETE'
    assert restarted.display_items()==[]

def test_shutdown_does_not_dispatch(setup):
    q,a,j=setup;request(q,j);a.worker.is_alive.return_value=False;q.close();q.step()
    a.start.assert_not_called()
