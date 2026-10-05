from app.pursuit_explanation import explain

def result(requirement='Build data platforms'):
    return {'score':78,'recommendation':'CASUAL','checks':[{'label':'Evidence score for applying','passed':True,'detail':'78/100; minimum 50'}],
            'core_evidence':[{'requirement_id':'r1','requirement':requirement,'alignment':'strong','citations':[{'source_title':'Platform story','source_path':'story.md','start_line':9,'quote':'I owned the ingestion roadmap.'}]},
                             {'requirement_id':'r2','requirement':'Manage international launches','alignment':'insufficient_evidence','citations':[]}]}

def test_explanation_is_job_specific_and_preserves_original_citations():
    run={'results':{'r1':{'judgment':{'assessment':{'review_required':True}}}}}
    a=explain(result(),run)
    b=explain(result('Build AI agents'),run)
    assert a['summary']!=b['summary']
    assert a['strengths'][0]['citations'][0]['quote']=='I owned the ingestion roadmap.'
    assert a['strengths'][0]['citations'][0]['start_line']==9
    assert a['strengths'][0]['tentative']
    assert a['gaps'][0]['requirement_id']=='r2'
    assert 'does not establish' in a['gaps'][0]['detail']

def test_uncited_strong_classification_is_not_a_tailoring_strength():
    r=result();r['core_evidence'][0]['citations']=[]
    assert explain(r,{})['strengths']==[]

def test_explicit_blocker_takes_precedence_in_explanation():
    r=result();r.update(recommendation='SKIP',blockers=[{'kind':'location','quote':'US only','reason':'Explicit mismatch'}])
    e=explain(r,{})
    assert 'Explicit preference conflict: location' in e['summary']
    assert e['blockers'][0]['quote']=='US only'
