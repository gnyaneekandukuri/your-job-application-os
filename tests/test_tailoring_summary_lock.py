import pytest
from app.tailoring import MasterChanges, apply_master_changes, apply_proposal, summary_ids
from app.retrieval import RetrievalError

REF=[{'source_id':'resume','quote':'Led launch'}]


def row(item_id,kind,text):
    return {'item_id':item_id,'kind':kind,'text':text,'citations':[],'reason':''}


MASTER=[row('m1','contact','Name'),row('m2','heading','Summary'),row('m3','text','My own positioning.'),
        row('m4','heading','Experience'),row('m5','role','Acme'),row('m6','bullet','Led launch')]


def test_summary_is_found_under_heading_or_before_first_heading():
    assert summary_ids(MASTER)=={'m3'}
    unheaded=[row('c','contact','Name'),row('s','text','Positioning.'),row('h','heading','Experience'),row('b','bullet','Led launch')]
    assert summary_ids(unheaded)=={'s'}
    assert summary_ids([row('t','text','No headings at all')])==set()


def test_draft_and_repair_edits_leave_summary_unchanged():
    edits=[{'item_id':'m3','text':'Rewritten summary','citations':REF,'reason':'JD'},
           {'item_id':'m6','text':'Led product launch','citations':REF,'reason':'JD'}]
    draft=apply_master_changes(MASTER,MasterChanges(edits=edits,questions=[]))
    texts={b.item_id:b.text for b in draft.blocks}
    assert texts['m3']=='My own positioning.'
    assert texts['m6']=='Led product launch'


@pytest.mark.parametrize('proposal',[
    {'operation':'replace','target_ids':['m3'],'after_id':'','text':'New summary'},
    {'operation':'remove','target_ids':['m3'],'after_id':'','text':''},
    {'operation':'add','target_ids':[],'after_id':'m3','text':'Extra line'},
])
def test_proposals_cannot_touch_summary(proposal):
    with pytest.raises(RetrievalError,match='summary stays'):
        apply_proposal(MASTER,{**proposal,'citations':REF,'reason':'JD','jd_quote':'q'},'1')
