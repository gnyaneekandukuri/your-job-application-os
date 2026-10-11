from types import SimpleNamespace
import json
import pytest
from app import resume_jd_review as review
from app.retrieval import RetrievalError

class Engine:
    def __init__(self):
        self.data, self.calls = {}, []
        self.result = dict(assessable=True, summary='Strong fit with some gaps.',
            role_alignment=32, ownership_scope=20, impact_specificity=16, clarity_emphasis=12,
            strengths=['Relevant product delivery'], gaps=['Scope could be clearer'],
            improvements=['Emphasize ownership'])
    def get(self, key): return self.data.get(key)
    def put(self, key, value): self.data[key] = value
    def paid(self, kind, payload):
        self.calls.append((kind,payload))
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.result)}]}]}

def inputs():
    return Engine(), SimpleNamespace(id='j',description='Own product strategy and delivery'), {
        'draft':{'blocks':[{'text':'Built and launched a product','citations':['SECRET'],'reason':'SECRET'}]},
        'context':{'stories':'SECRET'},'checks':'SECRET'}

def test_one_llm_call_visible_inputs_and_cached_score():
    e,j,r=inputs()
    result=review.evaluate(e,j,r)
    assert result['score']==80
    assert sum(d['points'] for d in result['dimensions'])==80
    assert review.evaluate(e,j,r)==result and len(e.calls)==1
    kind,payload=e.calls[0]
    assert kind=='tailoring' and payload['store'] is False
    assert 'SECRET' not in json.dumps(payload)
    assert set(json.loads(payload['input']))=={'job_description','resume'}
    assert payload['text']['format']['strict']

def test_edits_jd_and_rubric_invalidate_cache(monkeypatch):
    e,j,r=inputs(); k=review.cache_key(j,r)
    r['draft']['blocks'][0]['text']='Changed wording'
    assert review.cache_key(j,r)!=k
    k=review.cache_key(j,r);j.description+=' New duty'
    assert review.cache_key(j,r)!=k
    k=review.cache_key(j,r);monkeypatch.setattr(review,'RUBRIC','new rules')
    assert review.cache_key(j,r)!=k

def test_no_score_on_unassessable_inputs():
    e,j,r=inputs();e.result.update(assessable=False,summary='No substantive JD.')
    assert review.evaluate(e,j,r)['score'] is None

@pytest.mark.parametrize('points',[41,-1])
def test_invalid_score_not_cached_or_retried(points):
    e,j,r=inputs();e.result['role_alignment']=points
    with pytest.raises(RetrievalError):review.evaluate(e,j,r)
    assert len(e.calls)==1 and not e.data

def test_empty_or_oversize_does_not_call_provider():
    e,j,r=inputs();j.description=''
    with pytest.raises(RetrievalError):review.evaluate(e,j,r)
    j.description='x'*130000
    with pytest.raises(RetrievalError):review.evaluate(e,j,r)
    assert not e.calls
