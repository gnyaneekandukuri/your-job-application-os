import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.pursuit import recommend


def fixture(monkeypatch,percent=60,provisional=False,insufficient=False,blocker=False,role_match=True,core_count=1,missing_count=None):
    policy=json.loads(Path('criteria/pursuit-policy.json').read_text())
    score={'method':'dimensions','percent':percent,'pending':0,'provisional':provisional,'review_required_ids':['r'] if provisional else [],
           'dimensions':[{'id':d,'percent':percent} for d in ('actions','ownership','scope')]}
    monkeypatch.setattr('app.pursuit.score_report',lambda run:score)
    run={'state':'COMPLETE','requirements':[{'id':'r','category':'responsibility','quote':'Build products','interpretation':'Build products'}],
         'results':{'r':{'judgment':{'assessment':{'alignment':'insufficient_evidence' if insufficient else 'strong','dimensions':{}},'explanation':'Evidence'},'passages':[]}}}
    if missing_count is not None:
        from copy import deepcopy
        base=deepcopy(run['results']['r'])
        run['requirements']=[{**run['requirements'][0],'id':f'r{i}'} for i in range(core_count)]
        run['results']={f'r{i}':deepcopy(base) for i in range(core_count)}
        for i in range(missing_count):
            run['results'][f'r{i}']['judgment']['assessment']['alignment']='insufficient_evidence'
    class Signal(SimpleNamespace):
        def model_dump(self):return vars(self)
    interpretation=SimpleNamespace(core_requirement_ids=list(run['results']),constraints=[Signal(finding='incompatible',kind='location',reason='Explicit conflict')] if blocker else [],
                                   priorities=[Signal(kind='role_content',finding='match' if role_match else 'not_established')])
    return recommend(run,interpretation,policy)


@pytest.mark.parametrize('score,provisional,insufficient,expected',[(27,False,False,'REVIEW'),(90,True,False,'ATTACK_HARD'),(60,False,True,'REVIEW'),(50,False,False,'CASUAL'),(49,False,False,'REVIEW'),(80,False,False,'ATTACK_HARD')])
def test_evidence_gates(monkeypatch,score,provisional,insufficient,expected):
    assert fixture(monkeypatch,score,provisional,insufficient)['recommendation']==expected


def test_explicit_blocker_still_takes_precedence(monkeypatch):
    assert fixture(monkeypatch,27,True,True,True)['recommendation']=='SKIP'


def test_policy_migration_preserves_personal_thresholds(tmp_path):
    from app.user_workspace import initialize,write_json
    initialize(tmp_path)
    path=tmp_path/'criteria/pursuit-policy.json'
    p=json.loads(path.read_text());p['decision_rules'].pop('casual');p['decision_rules']['attack_hard']['minimum_overall']=88
    write_json(path,p);initialize(tmp_path)
    p=json.loads(path.read_text())
    assert p['decision_rules']['attack_hard']['minimum_overall']==88
    assert p['decision_rules']['casual']['minimum_overall']==50


def test_policy_migration_keeps_thresholds_but_removes_blanket_gates(tmp_path):
    from app.user_workspace import initialize, write_json
    initialize(tmp_path)
    path=tmp_path/'criteria/pursuit-policy.json'
    policy=json.loads(path.read_text())
    policy['schema_version']='0.3.0'
    policy['decision_rules']['casual'].update(minimum_overall=62, requires_nonprovisional_score=True, requires_role_content_match=True)
    write_json(path,policy); initialize(tmp_path)
    updated=json.loads(path.read_text())
    assert updated['decision_rules']['casual']['minimum_overall']==62
    assert not updated['decision_rules']['casual']['requires_nonprovisional_score']
    assert (tmp_path/'criteria/pursuit-policy-before-0.4.json').exists()


def test_decision_policy_does_not_invalidate_interpretation():
    from app.pursuit import interpretation_key
    assert interpretation_key('j',{'analysis':'a','profile':'p','report':'r','criteria':'old'}) == interpretation_key('j',{'analysis':'a','profile':'p','report':'r','criteria':'new'})
    assert interpretation_key('j',{'analysis':'a','profile':'p','report':'r'}) != interpretation_key('j',{'analysis':'a','profile':'changed','report':'r'})


def test_unknown_preference_alignment_allows_casual_not_high_priority(monkeypatch):
    result=fixture(monkeypatch,90,True,role_match=False)
    assert result['recommendation']=='CASUAL'
    assert 'alignment is not established' in result['rationale']


def test_partial_core_fit_is_allowed(monkeypatch):
    # Half the core work can be undocumented and the role can still merit applying.
    assert fixture(monkeypatch,60,True,core_count=4,missing_count=2)['recommendation']=='CASUAL'
    assert fixture(monkeypatch,60,True,core_count=4,missing_count=3)['recommendation']=='REVIEW'
