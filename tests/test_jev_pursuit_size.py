import json
import pytest
from app.jev_pursuit import evaluate_bounded
from app.jev_client import MODEL, POLICY
from app.retrieval import RetrievalError

class Client:
    def __init__(self):self.calls=[]
    def evaluate(self,jid,stage,state,questions):
        body={'model':MODEL,'state':state,'questions':questions}
        assert len(json.dumps(body,ensure_ascii=False,sort_keys=True).encode())<=POLICY['max_request_bytes']
        self.calls.append(body)
        return {k:{'choice':'yes'} for k in questions}

def test_split_keeps_every_question_and_full_context(monkeypatch):
    monkeypatch.setitem(POLICY,'max_request_bytes',500)
    client=Client();state={'text':'原始 experience'}
    questions={str(i):{'description':'x'*170} for i in range(7)}
    result=evaluate_bounded(client,'job',state,questions)
    assert set(result)==set(questions)
    assert len(client.calls)>1
    assert all(c['state']==state for c in client.calls)
    assert sum(len(c['questions']) for c in client.calls)==7

def test_preflight_rejects_oversized_single_question_before_any_dispatch(monkeypatch):
    monkeypatch.setitem(POLICY,'max_request_bytes',500)
    client=Client()
    with pytest.raises(RetrievalError):evaluate_bounded(client,'job',{}, {'small':{},'large':{'text':'x'*700}})
    assert not client.calls

def test_small_request_remains_one_call():
    client=Client();evaluate_bounded(client,'job',{}, {'one':{},'two':{}})
    assert len(client.calls)==1
