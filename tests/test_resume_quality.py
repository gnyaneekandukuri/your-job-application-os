import pytest
from app.resume_quality import (QualityChecks, FrameworkFinding, AuditedFinding, experience_indices, framework_summary, metric_coverage, recurring_revenue_conflict)
from app.tailoring import Block
from app.retrieval import RetrievalError

def blocks(n):
    return [{'item_id':str(i),'kind':'bullet','text':'For reporting, built a prototype that enabled self-service.'} for i in range(n)]

def findings(n,passed):
    return [FrameworkFinding(block_index=i,meets_framework=i<passed,context_quote='For reporting',action_quote='built a prototype',result_quote='enabled self-service',measurement_quote='',reason='Missing outcome' if i>=passed else 'Supported qualitative result') for i in range(n)]

def test_exact_eighty_percent_and_rounding():
    assert framework_summary(blocks(5),list(range(5)),findings(5,4),'PAR',set(range(5)))['meets_threshold']
    assert not framework_summary(blocks(5),list(range(5)),findings(5,3),'PAR',set(range(5)))['meets_threshold']
    q=framework_summary(blocks(6),list(range(6)),findings(6,4),'PAR',set(range(6)))
    assert q['required']==5 and not q['meets_threshold']

def test_qualitative_par_passes_but_xyz_needs_measurement():
    assert framework_summary(blocks(1),[0],findings(1,1),'PAR',{0})['passed']==1
    assert framework_summary(blocks(1),[0],findings(1,1),'XYZ',{0})['passed']==0
    assert framework_summary(blocks(1),[0],findings(1,1),'PAR',set())['passed']==0

def test_scope_excludes_education_and_additional_and_includes_original_bullets_after_style_edit():
    rows=[{'item_id':'h','kind':'heading','text':'PROFESSIONAL EXPERIENCE'},*blocks(2),{'item_id':'a','kind':'heading','text':'ADDITIONAL INFORMATION'},{'item_id':'v','kind':'bullet','text':'Volunteering'}]
    assert experience_indices(rows,rows)==[1,2]
    edited=[dict(b) for b in rows];edited[1]['kind']='text'
    assert experience_indices(edited,rows)==[1,2]

def test_framework_missing_duplicate_or_fabricated_components_cannot_pass():
    with pytest.raises(RetrievalError):framework_summary(blocks(2),[0,1],findings(1,1),'PAR',{0,1})
    f=findings(1,1);f[0].result_quote='Invented result'
    assert framework_summary(blocks(1),[0],f,'PAR',{0})['passed']==0

def test_metric_review_must_cover_every_number_and_failed_semantics_fail_claim():
    f=AuditedFinding(block_index=0,supported=True,reason='Checked',metrics=[{'claim':'20%','supported':True,'reason':'Supported'}])
    with pytest.raises(RetrievalError):metric_coverage(f,'Reduced time 20% across 3 teams')
    f.metrics[0].claim='Reduced time 20% across 3 teams';f.metrics[0].supported=False;f.metrics[0].reason='Source described projected savings'
    metric_coverage(f,'Reduced time 20% across 3 teams');assert not f.supported

def test_product_revenue_cannot_silently_become_arr_with_different_numeric_format():
    b=Block(kind='bullet',text='Drove $450K ARR.',citations=[],reason='')
    sources=[{'id':'e1','title':'Project story','text':'Customer commitments converted into approximately $450,000 in product revenue.'}]
    assert 'cannot be labeled ARR' in recurring_revenue_conflict(b,sources)
    b.text='Delivered $450K in product revenue.';assert not recurring_revenue_conflict(b,sources)

def test_strict_provider_schema_has_no_optional_properties():
    s=QualityChecks.model_json_schema()
    for obj in [s,*s.get('$defs',{}).values()]:
        if obj.get('type')=='object':assert set(obj['required'])==set(obj['properties'])


def test_confirmed_arr_resolution_is_scoped_to_source_and_amount():
    b=Block(kind='bullet',text='Drove $450K ARR.',citations=[],reason='')
    story={'id':'e1','path':'stories/product.md','text':'Customer commitments converted to $450,000 in product revenue.'}
    fact={'id':'candidate_fact_rules.facts.REVENUE','status':'USER_CONFIRMED','text':'Candidate confirmed $450K ARR.', 'revenue_resolution':{'type':'ARR','amount_usd':450000,'source_paths':['stories/product.md']}}
    assert not recurring_revenue_conflict(b,[story,fact])
    assert recurring_revenue_conflict(b,[{**story,'path':'stories/other.md'},fact])
    assert recurring_revenue_conflict(b,[story,{**fact,'status':'PENDING'}])
    assert recurring_revenue_conflict(b,[story,{**fact,'id':'untrusted-story'}])
    assert recurring_revenue_conflict(b,[story,{**fact,'revenue_resolution':{**fact['revenue_resolution'],'amount_usd':500000}}])
