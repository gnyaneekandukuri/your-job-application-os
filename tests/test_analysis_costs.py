import json
import sqlite3
from types import SimpleNamespace
import pytest
from app.analysis_context import select_passages, MAX_BYTES
from app.cost_tracking import token_cost, usage_scope
from app.retrieval import RetrievalService, RetrievalError


def test_hybrid_selection_keeps_semantic_match_without_keywords():
    passages=[{'id':str(i),'text':('Own operational workloads and customer outcomes. '*30), 'raw_vector':[0.,1.]} for i in range(40)]
    passages[30]={'id':'semantic','text':'Built autonomous tool execution and orchestration.','raw_vector':[1.,0.]}
    chosen=select_passages(passages,['AI agents'],[[1.,0.]])
    assert chosen[0]['id']=='semantic'
    assert len(chosen)<=4
    assert all('raw_vector' not in p for p in chosen)
    assert len(json.dumps(chosen,ensure_ascii=False).encode())<=MAX_BYTES


def test_each_requirement_gets_retrieval_and_original_text():
    passages=[{'id':str(i),'text':f'Unique evidence {i}', 'raw_vector':[float(i==0),float(i==1)]} for i in range(2)]
    chosen=select_passages(passages,['a','b'],[[1.,0.],[0.,1.]])
    assert {p['id'] for p in chosen}=={'0','1'}
    assert [p['text'] for p in chosen]==[p['text'] for p in passages]


def test_oversized_evidence_does_not_fall_back_to_full_corpus():
    with pytest.raises(RetrievalError):
        select_passages([{'id':'large','text':'x'*60000,'raw_vector':[1.,0.]}],['q'],[[1.,0.]])


def test_cache_rates_and_attribution_are_recorded(tmp_path):
    path=tmp_path/'usage.sqlite3'
    # Existing ledger migrates without rewriting history.
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE calls(id INTEGER PRIMARY KEY,kind TEXT,model TEXT,created_at TEXT,state TEXT,reserved REAL,cost REAL,input_tokens INTEGER,output_tokens INTEGER)')
        db.execute("INSERT INTO calls VALUES(1,'evidence','gpt-6-sol','old','RECEIVED',0,.5,100,10)")
    class Provider:
        configured=True
        def request(self,*args):
            return {'usage':{'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':600,'cache_write_tokens':200}}}
    engine=RetrievalService(path,tmp_path/'source.md',Provider())
    with usage_scope('job-1','evidence_batch'):
        engine.paid('evidence',{'model':'gpt-6-sol','input':'sample','max_output_tokens':100})
    with sqlite3.connect(path) as db:
        row=db.execute('SELECT job_id,stage,cached_tokens,cache_write_tokens,cost FROM calls WHERE id=2').fetchone()
        assert row[:4]==('job-1','evidence_batch',600,200)
        assert row[4]==pytest.approx(.00202)
        assert db.execute('select cost,job_id from calls where id=1').fetchone()==(.5,None)
    assert token_cost('unknown',1000,100,{})[1]=='unverified_model_estimate'


def test_failed_calls_are_not_presented_as_zero_cost(tmp_path):
    class Provider:
        configured=True
        def request(self,*args): raise RetrievalError('Credits exhausted (HTTP 429)',502)
    engine=RetrievalService(tmp_path/'usage.sqlite3',tmp_path/'source.md',Provider())
    with pytest.raises(RetrievalError), usage_scope('job-2','requirements'):
        engine.paid('requirements',{'model':'gpt-6-sol','input':'sample'})
    with sqlite3.connect(engine.path) as db:
        row=db.execute('select cost,state,job_id,error from calls').fetchone()
        assert row[:3]==(None,'FAILED_OR_UNKNOWN','job-2')
        assert '429' in row[3]
