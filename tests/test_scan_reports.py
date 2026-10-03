import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.cost_tracking import scan_id


def test_scan_receipt_preserves_progress_and_distinguishes_old_history(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        with d.db() as db:
            db.execute('INSERT INTO scans VALUES(1,?,?,?)',('2026-10-02', '2026-10-02',json.dumps({'state':'complete','new_count':8})))
            db.execute('INSERT INTO scans VALUES(2,?,?,?)',('2026-10-03',None,json.dumps({'state':'running','tracking_version':1,'new_count':1,'sources':{'yc':{'state':'checked','count':1}}})))
        token=scan_id.set(2)
        try:
            role={'id':'a','company':'Example','title':'PM'}
            d.record_scan_role(role,stage='Discovered')
            d.record_scan_role(role,priority='High priority',reason='Relevant experience')
            d.record_scan_role(role,stage='Assessment requested',assessment_requested=True)
            d.record_scan_role(role,job_id='job',assessment={'state':'COMPLETE','recommendation':'CASUAL'})
        finally:scan_id.reset(token)
        report=client.get('/api/assessments').json()['scans']
        assert report[0]['tracked'] and not report[1]['tracked']
        assert len(report[0]['roles'])==1
        assert report[0]['roles'][0]['assessment_requested']
        assert report[0]['roles'][0]['assessment']['state']=='COMPLETE'
    with TestClient(create_app(tmp_path)) as client:
        report=client.get('/api/assessments').json()['scans'][0]
        assert report['state']=='interrupted'
        assert report['new_count']==1
        assert report['sources']['yc']['count']==1
        assert report['roles'][0]['priority']=='High priority'
