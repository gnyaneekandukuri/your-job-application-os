"""Priority mass, cache replay, and presentation regression coverage."""
import json
from types import SimpleNamespace
import pytest
from app.jev_client import MODEL, VERSION, JevClient
from app.jev_pursuit import interpret
from app.pursuit import interpretation_key
from app.retrieval import digest
from test_jev_assessment import answer_body, engine_at
from test_pursuit_decisions import fixture


@pytest.mark.parametrize('mass,inconsistent,expected', [(.99,False,'match'),(.79,False,'not_established'),(.8,False,'match'),(.99,True,'not_established')])
def test_priority_mass_preserves_constraint_single_block_rule(mass,inconsistent,expected):
    job=SimpleNamespace(id='j',source_url=None,description='Own product strategy.\nBuild platform products.')
    run={'requirements':[{'id':'r','category':'responsibility'}]}
    profile={'candidate':{'work_authorization':{'requires_h1b_transfer':True}}}
    class Client:
        def evaluate(self,jid,stage,state,questions):
            answers=answer_body({'questions':questions,'state':state})['answers']
            for name,a in answers.items():
                if name.startswith('priority_'):
                    a.update(choice='s1',confidence=.5,probabilities={'not_established':1-mass,'s1':mass*.51,'s2':mass*.49},provider_inconsistent=inconsistent)
                if name=='constraint_role_content':
                    a.update(choice='incompatible__s1',confidence=.5)
            return answers
    parsed=interpret(Client(),job,run,profile)
    assert all(p.finding==expected for p in parsed.priorities)
    assert all(p.quote==('Own product strategy.' if expected=='match' else '') for p in parsed.priorities)
    assert next(c for c in parsed.constraints if c.kind=='role_content').finding=='unknown'


def test_new_interpretation_key_replays_identical_request_without_dispatch(tmp_path,monkeypatch):
    import app.pursuit as pursuit
    engine=engine_at(tmp_path)
    job=SimpleNamespace(id='j',source_url=None,description='Own product strategy.\nBuild platform products.')
    run={'requirements':[{'id':'r','category':'responsibility'}]}
    profile={'candidate':{'work_authorization':{'requires_h1b_transfer':True}}}
    class Seed:
        def evaluate(self,jid,stage,state,questions):
            body={'model':MODEL,'state':state,'questions':questions}
            response=answer_body(body)
            for name,a in response['answers'].items():
                if name.startswith('priority_'):
                    a.update(choice='s1',confidence=.5,probabilities={'not_established':.01,'s1':.5,'s2':.49})
            key='jev-assessment:'+digest(VERSION+json.dumps(body,ensure_ascii=False,sort_keys=True))
            engine.put(key,{'response':response})
            return response['answers']
    interpret(Seed(),job,run,profile)
    hashes={'analysis':'a','profile':'p','report':'r'}
    old='pursuit-signals:j:'+digest(pursuit.VERSION+pursuit.MODEL+pursuit.PROMPT+json.dumps(hashes,sort_keys=True))
    engine.put(old,{'old':True})
    assert interpretation_key('j',hashes)!=old
    assert engine.get(interpretation_key('j',hashes)) is None
    def fail(body):pytest.fail('Cache replay must never dispatch')
    parsed=interpret(JevClient(engine,post=fail),job,run,profile)
    role=next(p for p in parsed.priorities if p.kind=='role_content')
    assert role.finding=='match' and role.quote=='Own product strategy.'
    # The service must ignore both previous signal and legacy interpretation keys.
    from langgraph.checkpoint.memory import MemorySaver
    policy={'decision_rules':{}}
    legacy_hashes={**hashes,'criteria':digest(json.dumps(policy,sort_keys=True))}
    legacy_version=digest(pursuit.VERSION+pursuit.MODEL+pursuit.PROMPT+json.dumps(legacy_hashes,sort_keys=True))
    engine.put('pursuit-interpretation:j:'+legacy_version, {'old':True})
    run['source_versions'] = {'jd':'source'}
    monkeypatch.setattr(pursuit, 'recommend', lambda run, parsed, policy: {'recommendation':'ATTACK_HARD'})
    manager=SimpleNamespace(engine=engine, jev_client=lambda:JevClient(engine,post=fail))
    service=pursuit.PursuitService(manager)
    inputs=('new-version',run,profile,policy,hashes)
    service.graph(MemorySaver(),job,inputs).invoke({'version':'new-version'},service.config(job,'new-version'))
    assert next(p for p in engine.get(interpretation_key('j',hashes))['priorities'] if p['kind']=='role_content')['finding']=='match'
    with engine.connect() as db:
        assert db.execute('SELECT count(*) FROM jev_assessment_calls').fetchone()[0]==0


def test_optional_alignment_is_explicit_and_nonblocking(monkeypatch):
    result=fixture(monkeypatch,90,role_match=False)
    check=next(c for c in result['checks'] if c['label']=='Target work alignment')
    assert check['passed'] and check['state']=='not_required'
    assert 'Not required' in check['detail']
    check=next(c for c in result['checks'] if c['label']=='Responsibilities match target work')
    assert not check['passed'] and check['state']=='failed'


def test_report_miss_raises_before_dispatch_or_write(tmp_path):
    from scripts.recompute_pursuit_report import ReadOnlyCache, CacheMiss, cached_client
    from app.jev_client import choice
    engine=engine_at(tmp_path)
    with pytest.raises(CacheMiss):
        cached_client(ReadOnlyCache(engine.path)).evaluate('j','pursuit_interpretation',{}, {'q':choice('Pick',{'yes':'Yes','no':'No'})})
    with engine.connect() as db:
        assert db.execute("SELECT count(*) FROM cache WHERE key LIKE 'jev-assessment:%'").fetchone()[0]==0
