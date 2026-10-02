import httpx
import pytest
from app.model_provider import OpenAIProvider, failure_message
from app.retrieval import RetrievalError


@pytest.mark.parametrize('code,expected',[
    ('credit_balance_exhausted','credits are exhausted'),
    ('project_spend_limit_exceeded','project spending limit'),
    ('organization_spend_limit_exceeded','organization spending limit'),
    ('organization_usage_limit_exceeded','usage allowance'),
    ('insufficient_quota','not a temporary'),
    ('rate_limit_exceeded','temporary request/token'),
    ('slow_down','asked us to slow down'),
])
def test_specific_error_code_wins_over_general_type(code,expected):
    response=httpx.Response(429,json={'error':{'code':code,'type':'insufficient_quota','message':'PRIVATE RAW BODY'}})
    message=failure_message(response)
    assert expected in message
    assert 'PRIVATE' not in message
    assert '(HTTP 429)' in message


def test_unknown_and_malformed_errors_do_not_claim_billing():
    for body in ({'error':{'code':{'secret':'data'}}}, {'error':[]}, []):
        assert 'could not be determined' in failure_message(httpx.Response(429,json=body))
    assert 'could not be determined' in failure_message(httpx.Response(429,text='not json'))
    response=httpx.Response(429,json={'error':{'type':'rate_limit_error'}},headers={'retry-after':'30'})
    assert '30 seconds' in failure_message(response)


def test_provider_does_not_retry_or_leak_error_body(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs):
            calls.append(1)
            return httpx.Response(429,json={'error':{'code':'insufficient_quota','message':'secret'}})
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    monkeypatch.setattr('app.model_provider.httpx.Client',Client)
    with pytest.raises(RetrievalError,match='quota is unavailable'):
        OpenAIProvider().request('responses',{'input':'test'})
    assert len(calls)==1
