import json
import re
from types import SimpleNamespace
import pytest
from app.discovery_location import location_match, COUNTRIES
from app.read_snapshot import read_snapshot, snapshot_cached
from app.discovery import Discovery
from app.retrieval import RetrievalService


@pytest.mark.parametrize('location,status',[('New York, USA','match'),('Bengaluru, Karnataka, IN','outside'),('South Africa','outside'),('San Francisco, CA','unclear'),('Remote','unclear'),('Remote except USA','unclear'),('US','match'),('United States of America','match'),('Canada','outside')])
def test_location_semantics(location,status):
    assert location_match(location,{'location_filter':{'countries':['US'],'label':'USA'}})['status']==status


def test_all_country_names_still_recognized_without_compiler_churn(monkeypatch):
    # Re's global cache is smaller than the country pattern set. Compiled objects
    # must survive cache eviction instead of being rebuilt for each listing.
    original=re._compiler.compile
    compilations=[]
    def compile(*args,**kwargs):
        compilations.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(re._compiler,'compile',compile)
    re.purge()
    for country in COUNTRIES:
        result=location_match(country['name'],{'location_filter':{'countries':[country['code']]}})
        assert country['code'] in result['countries']
    assert len(compilations)<10


def test_read_snapshot_expires_and_retains_argument_identity():
    class Reader:
        calls=0
        @snapshot_cached
        def get(self,value):
            self.calls+=1
            return value.text
    reader=Reader();value=SimpleNamespace(text='before')
    @read_snapshot
    def response():
        assert reader.get(value)==reader.get(value)
        other=SimpleNamespace(text='other')
        assert reader.get(other)=='other'
    response();assert reader.calls==2
    value.text='after';response();assert reader.calls==4
    assert reader.get(value)=='after';assert reader.calls==5


def test_table_payload_preserves_role_actions_without_repeating_evidence(tmp_path):
    raw={'items':[{'id':'x','content_hash':'hash','description':'Full JD','selected_assessment':{'state':'QUEUED'},
                  'keyword_match':{'priority':'worth_checking','label':'Worth checking','reason':'why','experience':[{'term':'AI','sources':[{'text':'private source'*1000}]}]},
                  'description_provenance':{'draft':'duplicate JD'},'initial_evaluation':{'unused':'values'}}],
         'keyword_profile':{'target':{'roles':['PM']},'experience':['large source data']}}
    d=Discovery.__new__(Discovery);d.view=lambda:raw
    import sqlite3
    d.path=tmp_path/"discovery.sqlite3"
    with sqlite3.connect(d.path) as db: db.execute("CREATE TABLE scans (id INTEGER,started TEXT,finished TEXT,payload TEXT)")
    d.assessment_results=lambda:{"counts":{"complete":1},"queued_count":2}
    result=d.table_view()
    item=result['items'][0]
    assert item['keyword_match']['experience']==[{'term':'AI'}]
    assert item['description']=='Full JD' and item['content_hash']=='hash'
    assert item['selected_assessment']['state']=='QUEUED'
    assert 'initial_evaluation' not in item and 'description_provenance' not in item
    assert result['keyword_profile']=={'target':{'roles':['PM']}}


def test_bulk_cache_read_keeps_missing_keys_and_large_batches_safe(tmp_path):
    engine=RetrievalService(tmp_path/'cache.sqlite3',tmp_path/'story.md',SimpleNamespace(configured=False))
    engine.put('one',{'value':1});engine.put('two',None)
    assert engine.get_many(['one','two',*map(str,range(1100))])=={'one':{'value':1},'two':None}
    assert engine.get_many([])=={}
