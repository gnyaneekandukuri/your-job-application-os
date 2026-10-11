import io
import base64
import threading
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.models import JobInput
from app.applications import Applications, ConfirmApplication, UpdateApplication, ManualApplication
from app.retrieval import RetrievalError


def test_application_workflow_and_snapshot(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        state = client.app.state
        review = state.service.create(JobInput(request_id=uuid4(), company='Example Labs', title='PM', description='Build reporting tools for enterprise customers.'))
        job = review.job
        resume = {'state':'APPROVED', 'revision':'resume-v1', 'framework':'PAR',
                  'draft':{'blocks':[{'kind':'bullet','text':'Built reports for 12 users.'}]},
                  'context':{'assessment_requirements':['Reporting']}}
        original = state.tailoring
        state.tailoring = SimpleNamespace(lock=threading.Lock(), current=lambda j, revision: ('key', resume))
        headers = {'X-Local-Review':'1'}
        payload = dict(revision='resume-v1', confirmed=True, applied_date='2026-10-08')
        endpoint = f'/api/jobs/{job.id}/applications'
        assert client.post(endpoint,json=payload).status_code == 403
        assert client.post(endpoint,json={**payload,'confirmed':False},headers=headers).status_code == 422
        resume['state']='DRAFT'
        assert client.post(endpoint,json=payload,headers=headers).status_code == 409
        resume['state']='APPROVED'
        response=client.post(endpoint,json=payload,headers=headers)
        assert response.status_code == 200, response.text
        row=response.json()
        resume['draft']['blocks'][0]['text']='Later edit must not replace submitted resume.'
        assert client.post(endpoint,json=payload,headers=headers).json()['id']==row['id']
        assert len(client.get('/api/applications').json())==1
        assert '12 users' in client.get(f"/api/applications/{row['id']}/resume").text
        update=dict(revision=row['revision'],status='Interviewing',follow_up_date='2026-10-15',notes='=HYPERLINK("unsafe")\nFollow up & ask <question>')
        assert client.post(f"/api/applications/{row['id']}",json=update,headers=headers).status_code==200
        assert client.post(f"/api/applications/{row['id']}",json=update,headers=headers).status_code==409
        export=client.get('/api/applications/export')
        assert export.status_code==200
        with ZipFile(io.BytesIO(export.content)) as workbook:
            for name in workbook.namelist(): ET.fromstring(workbook.read(name))
            sheet=ET.fromstring(workbook.read('xl/worksheets/sheet1.xml'))
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            assert not sheet.findall('.//s:f',ns)
            assert sheet.find('.//s:c[@r="I2"]/s:is/s:t',ns).text==update['notes']
        saved=Applications(tmp_path/'applications.sqlite3')
        assert saved.list()[0]['status']=='Interviewing'
        assert saved.get(row['id'])['job_snapshot']['description']==job.description
        assert saved.get(row['id'])['assessment_snapshot']['assessment_requirements']==['Reporting']
        state.tailoring=original


def test_empty_workbook_and_missing_record(tmp_path):
    store=Applications(tmp_path/'applications.sqlite3')
    with ZipFile(io.BytesIO(store.export())) as workbook:
        assert b'A1:J1' in workbook.read('xl/worksheets/sheet1.xml')
    with pytest.raises(RetrievalError): store.resume('unknown')


def test_applied_without_resume_is_durable_and_exports(tmp_path):
    store=Applications(tmp_path/'applications.sqlite3')
    snapshot={'company':'Example','title':'PM','url':'https://example.com/job','description':'JD'}
    data=ManualApplication(confirmed=True,applied_date='2026-10-11',notes='Used my own resume')
    row=store.confirm_without_resume('opportunity:123',snapshot,data,{'boards':['yc']})
    assert row['recorded_without_resume'] and not row['has_pdf']
    assert row['resume_revision']=='' and row['notes']==data.notes
    assert store.confirm_without_resume('opportunity:123',snapshot,data)['id']==row['id']
    assert len(Applications(store.path).list())==1
    assert store.get(row['id'])['job_snapshot']==snapshot
    with pytest.raises(RetrievalError,match='No resume'):store.resume(row['id'])
    with ZipFile(io.BytesIO(store.export())) as archive:
        assert b'Used my own resume' in archive.read('xl/worksheets/sheet1.xml')


def test_application_uses_downloaded_pdf_even_after_draft_changes(tmp_path):
    store=Applications(tmp_path/'applications.sqlite3')
    pdf=b'%PDF-frozen-content'
    frozen={'state':'EDITED','revision':'downloaded-v1','framework':'PAR','draft':{'blocks':[{'kind':'text','text':'Submitted edited text'}]},'context':{}}
    def current(*args):
        raise AssertionError('Must not read a later draft for a downloaded version')
    tailoring=SimpleNamespace(lock=threading.Lock(),current=current,exported=lambda j,r:{'record':frozen,'pdf':base64.b64encode(pdf).decode()} if r=='downloaded-v1' else None)
    job=SimpleNamespace(id='job-1',company='Example',title='PM',source_url='',model_dump=lambda **kw:{'description':'Saved JD'})
    row=store.confirm(job,tailoring,ConfirmApplication(revision='downloaded-v1',confirmed=True,applied_date='2026-10-08'))
    assert row['has_pdf'] is True
    assert 'resume_pdf' not in row
    assert store.pdf(row['id'])==pdf
    assert 'Submitted edited text' in store.resume(row['id'])
