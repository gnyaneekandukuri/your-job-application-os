import json
import sqlite3
import httpx
import pytest
from app.cost_tracking import scan_id
from app.discovery_jev import JevClassifier, POLICY
from app.usage_dashboard import usage_report


def seed(directory):
    with sqlite3.connect(directory/'reviews.sqlite3') as db:
        db.execute('CREATE TABLE jobs(id TEXT,payload TEXT)')
    with sqlite3.connect(directory/'discovery.sqlite3') as db:
        db.executescript('CREATE TABLE opportunities(id TEXT,payload TEXT,job_id TEXT); CREATE TABLE scans(id INTEGER,started TEXT,finished TEXT,payload TEXT);')
        db.execute('INSERT INTO opportunities VALUES(?,?,?)',('role',json.dumps({'company':'Example','title':'PM'}),None))
        db.execute('INSERT INTO scans VALUES(?,?,?,?)',(9,'2026-10-02T15:00:00Z',None,json.dumps({'state':'running','cost_tracking_version':1})))
        db.execute('INSERT INTO scans VALUES(?,?,?,?)',(8,'2026-10-01T15:00:00Z','2026-10-01T16:00:00Z',json.dumps({'state':'complete'})))


def response():
    policy=json.loads(POLICY.read_text())
    return {'model':policy['model'],'usage':{'input_tokens':1000},'answers':{
        name:{'type':'choice','choice':next(iter(q['criteria'])),'confidence':1.,
              'probabilities':{k:float(k==next(iter(q['criteria']))) for k in q['criteria']}}
        for name,q in policy['questions'].items()}}


def test_discovery_ledger_tracks_errors_usage_and_cached_reads(tmp_path,monkeypatch):
    seed(tmp_path)
    monkeypatch.setenv('TYPESAFE_API_KEY','test')
    payload=response()
    invoked=[]
    def post(url,**kwargs):
        invoked.append(1)
        return httpx.Response(200,json=payload,request=httpx.Request('POST',url))
    classifier=JevClassifier(tmp_path/'discovery.sqlite3',post)
    item={'id':'role','title':'PM','description':'Own the platform roadmap. '*20}
    token=scan_id.set(9)
    try:
        assert classifier.classify(item)['state']=='classified'
        assert classifier.classify(item)['cached']
        payload['model']='wrong'
        assert classifier.classify({**item,'title':'Bad response'})['state']=='error'
        classifier.post=lambda url,**kw:httpx.Response(429,request=httpx.Request('POST',url))
        assert classifier.classify({**item,'title':'No usage'})['state']=='error'
    finally:
        scan_id.reset(token)
    report=usage_report(tmp_path)
    assert len(report['calls'])==3  # Cached response is not counted twice.
    assert len(invoked)==2
    assert all(c['scan_id']==9 and c['company']=='Example' for c in report['calls'])
    assert sum(c['cost'] or 0 for c in report['calls'])==pytest.approx(.000084)
    assert sum(c['cost'] is None for c in report['calls'])==1
    assert any(c['state']=='FAILED_OR_UNKNOWN' and c['input_tokens']==1000 for c in report['calls'])
    assert [s['tracked'] for s in report['scans']]==[True,False]
    # Reading the dashboard does not call providers or change recorded usage.
    assert usage_report(tmp_path)==report
    assert len(invoked)==2


def test_legacy_success_remains_visible_without_invented_scan(tmp_path):
    seed(tmp_path)
    JevClassifier(tmp_path/'discovery.sqlite3')
    with sqlite3.connect(tmp_path/'discovery.sqlite3') as db:
        db.execute('INSERT INTO jev_classifications VALUES(?,?)',('old',json.dumps({
            'checked_at':'2026-10-01T15:30:00Z','model':'old','input_tokens':100,'estimated_cost_usd':.01})))
    result=usage_report(tmp_path)
    assert len(result['calls'])==1
    assert result['calls'][0]['cost']==.01
    assert result['calls'][0].get('scan_id') is None  # Even though its time overlaps scan #8.
