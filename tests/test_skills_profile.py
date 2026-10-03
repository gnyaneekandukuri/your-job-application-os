import json
from types import SimpleNamespace
import pytest
from app.skills_profile import SkillsProfile, LinkedInInput, SkillEdit, ResumeInput, passages
from app.retrieval import RetrievalService, RetrievalError

class Provider:
    configured=True
    def __init__(self):self.calls=[];self.bad=False;self.fail=False
    def request(self,endpoint,payload):
        assert endpoint=='responses'
        self.calls.append(payload)
        if self.fail:raise RetrievalError('Provider unavailable',502)
        if payload['text']['format']['name']=='skill_groups':
            entries=json.loads(payload['input'])
            return {'status':'completed','usage':{'input_tokens':100,'output_tokens':50},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'groups':[{'name':e['name'],'members':[e['id']]} for e in entries]})}]}]}
        text=json.loads(payload['input'])['passage']
        skill={'name':'Data product discovery','aliases':['Discovery for data products'],'activity':'Interviewed users to define data products.',
               'support':'described','quote':'Invented quote' if self.bad else text}
        return {'status':'completed','usage':{'input_tokens':100,'output_tokens':50},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'skills':[skill]})}]}]}

@pytest.fixture
def profile(tmp_path):
    provider=Provider();engine=RetrievalService(tmp_path/'retrieval.sqlite3',tmp_path/'unused',provider)
    sources=[{'id':'story','title':'Project story','employer':'Example','project':'Data product','path':'story.md','text':'I interviewed users and defined a data product.'}]
    service=SkillsProfile(SimpleNamespace(engine=engine,root=tmp_path),provider,lambda:list(sources))
    service.consolidate=lambda sources:None
    return service,provider,sources

def test_incremental_extraction_and_costs(profile):
    s,p,sources=profile
    assert s.view()['pending_passages']==1 and not p.calls
    s.build();assert s.view()['current'];assert len(p.calls)==1
    s.build();assert len(p.calls)==1
    sources.append({**sources[0],'id':'second','text':'I led customer interviews for a new platform.'})
    assert s.view()['pending_passages']==1
    s.build();assert len(p.calls)==2 and len(s.view()['skills'][0]['sources'])==2
    usage=s.engine.usage();assert usage['calls']==2 and usage['input_tokens']==200 and usage['estimated_cost_usd']>0

def test_edit_and_removed_sources_drop_old_evidence(profile):
    s,p,sources=profile;s.build()
    sources[0]['text']='A revised account of conducting customer interviews.'
    assert s.view()['skills']==[]
    s.build();assert len(p.calls)==2
    sources.clear();assert s.view()['skills']==[]

def test_unsupported_quote_stops_visibly(profile):
    s,p,_=profile;p.bad=True;s.build()
    assert s.state()['status']=='error'
    assert s.view()['pending_passages']==1 and not s.view()['skills']
    assert s.engine.usage()['calls']==1
    s.view();s.view();assert len(p.calls)==1

def test_partial_passages_reused_after_failure(profile):
    s,p,sources=profile;sources[0]['text']='a'*9000+'\n\n'+'b'*9000
    original=p.request
    def request(endpoint,payload):
        if len(p.calls)==1: p.fail=True
        return original(endpoint,payload)
    p.request=request;s.build()
    assert s.state()['status']=='error' and s.view()['pending_passages']==1
    p.request=original;p.fail=False;s.build()
    assert s.view()['current'] and len(p.calls)==3

def test_linkedin_skills_are_listed_no_api(profile):
    s,p,_=profile
    s.linkedin(LinkedInInput(url='https://www.linkedin.com/in/example',skills='SQL\nPython'))
    assert len(s.view()['skills'])==2 and not p.calls
    assert all(x['sources'][0]['support']=='listed' for x in s.view()['skills'])
    s.linkedin(LinkedInInput(skills='Python'))
    assert [x['name'] for x in s.view()['skills']]==['Python']

def test_linkedin_invalid_destination_and_import_fallback(profile,monkeypatch):
    from app.safe_fetch import ExtractionError
    s,p,_=profile
    with pytest.raises((RetrievalError,ExtractionError)):s.linkedin(LinkedInInput(url='https://example.com/in/person'))
    monkeypatch.setattr('app.safe_fetch.fetch_public',lambda _: (_ for _ in ()).throw(ExtractionError('Blocked')))
    result=s.linkedin(LinkedInInput(url='https://www.linkedin.com/in/person',skills='SQL',fetch_public=True))
    assert 'No readable' in result['message'] and s.view()['linkedin']['skills']=='SQL'

def test_corrections_survive_rebuild_and_hide_restore(profile):
    s,p,_=profile;s.build();key=s.view()['skills'][0]['key']
    s.edit(SkillEdit(key=key,name='Customer discovery',hidden=True));s.build()
    assert s.view()['skills'][0]['hidden'] and len(p.calls)==1
    s.edit(SkillEdit(key=key,name='Customer discovery',hidden=False))
    assert not s.view()['skills'][0]['hidden']
    s.edit(SkillEdit(name='SQL'));assert len(s.view()['skills'])==2

def test_chunks_bounded_and_source_complete():
    text='a'*25000
    chunks=passages(text);assert max(map(len,chunks))<=10000 and ''.join(chunks)==text

def test_interrupted_run_does_not_silently_resume(profile):
    s,p,sources=profile
    s.engine.put('skills:state',{'status':'running','enabled':True})
    restarted=SkillsProfile(s.library,p,lambda:sources)
    assert restarted.state()['status']=='interrupted' and not p.calls


def test_consolidation_cached_and_keeps_all_members(profile):
    s,p,sources=profile;s.build()
    SkillsProfile.consolidate(s,sources)
    count=len(p.calls);SkillsProfile.consolidate(s,sources)
    assert len(p.calls)==count
    assert 'data product discovery' in s.engine.get('skills:groups')


def test_resume_replacement_and_restore_master(profile):
    import base64
    s,p,sources=profile
    sources[0]['id']='current-resume'
    s.start=lambda:None
    s.upload_resume(ResumeInput(filename='resume.txt',content_base64=base64.b64encode(b'I build products and manage roadmaps.').decode(),consent_to_send=True))
    assert [x['id'] for x in s.snapshot()[0]]==['skills-resume']
    s.remove_resume()
    assert [x['id'] for x in s.snapshot()[0]]==['current-resume']


def test_public_linkedin_reads_explicit_skills_only(profile,monkeypatch):
    from app.safe_fetch import Page
    s,p,_=profile
    html=b'<script type="application/ld+json">{"@type":"Person","knowsAbout":["SQL","Product discovery"]}</script><p>Other advertised jobs need Java</p>'
    monkeypatch.setattr('app.safe_fetch.fetch_public',lambda url:Page(url,html,'text/html'))
    s.linkedin(LinkedInInput(url='https://www.linkedin.com/in/person',fetch_public=True))
    assert s.view()['linkedin']['skills']=='SQL\nProduct discovery'
    assert not p.calls
