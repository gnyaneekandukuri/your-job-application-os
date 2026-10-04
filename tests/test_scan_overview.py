from app.scan_overview import overview, blocker
from app.discovery_location import location_match
import pytest


@pytest.mark.parametrize('location,status',[
    ('San Francisco / New York City','match'),('San Francisco / Remote','unclear'),
    ('San Francisco / London','unclear'),('San Francisco / Atlantis','unclear'),
    ('San Francisco / New York City excluding USA','unclear')])
def test_city_lists_require_every_segment_known(location,status):
    assert location_match(location,{'location_filter':{'countries':['US']}})['status']==status


def test_latest_scan_explains_high_priority_holds_and_hidden_completed_results():
    items=[{'id':'a','company':'A','description_changed':True},
           {'id':'b','company':'B','assessment':{'state':'STALE'}},
           {'id':'c','company':'C','location_match':{'status':'unclear'}}]
    for i in items:i.update(title='PM',job_id='job')
    result={'settings':{'auto_assess':True},'run_reports':[{'started':'today','state':'incomplete','tracked':True,
        'new_count':0,'sources':{'yc':{'state':'partial'},'linkedin':{'state':'error','error':'Login expired'}},
        'roles':[{'id':i['id'],'priority':'High priority'} for i in items]}],
        'assessment_results':{'items':[{'assessment':{'state':'COMPLETE','recommendation':'REVIEW'}}]}}
    o=overview(result,items)
    assert o['high']==3 and o['requested']==0
    assert o['completed']==1 and o['worth_applying']==0
    assert [a['action'] for a in o['attention']]==['jd','assessment','listing']
    assert o['failures']==[{'board':'linkedin','state':'error','reason':'Login expired'}]
    assert not o['auto_paused']

