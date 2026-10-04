import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.pursuit import recommend


def fixture(monkeypatch,percent=60,provisional=False,insufficient=False,blocker=False):
    policy=json.loads(Path('criteria/pursuit-policy.json').read_text())
    score={'method':'dimensions','percent':percent,'pending':0,'provisional':provisional,'review_required_ids':['r'] if provisional else [],
           'dimensions':[{'id':d,'percent':percent} for d in ('actions','ownership','scope')]}
    monkeypatch.setattr('app.pursuit.score_report',lambda run:score)
    run={'state':'COMPLETE','requirements':[{'id':'r','category':'responsibility','quote':'Build products','interpretation':'Build products'}],
         'results':{'r':{'judgment':{'assessment':{'alignment':'insufficient_evidence' if insufficient else 'strong','dimensions':{}},'explanation':'Evidence'},'passages':[]}}}
    class Signal(SimpleNamespace):
        def model_dump(self):return vars(self)
    interpretation=SimpleNamespace(core_requirement_ids=['r'],constraints=[Signal(finding='incompatible',kind='location',reason='Explicit conflict')] if blocker else [],
                                   priorities=[Signal(kind='role_content',finding='match')])
    return recommend(run,interpretation,policy)


@pytest.mark.parametrize('score,provisional,insufficient,expected',[(27,False,False,'REVIEW'),(90,True,False,'REVIEW'),(60,False,True,'REVIEW'),(50,False,False,'CASUAL'),(49,False,False,'REVIEW'),(80,False,False,'ATTACK_HARD')])
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
