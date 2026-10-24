from app.retrieval import RetrievalService
from app.read_snapshot import read_snapshot
import sqlite3
from contextlib import closing


def test_usage_aggregation_is_exact_and_read_once_per_snapshot(tmp_path):
    engine=object.__new__(RetrievalService)
    engine.path=tmp_path/'usage.sqlite3'
    with sqlite3.connect(engine.path) as db:
        db.execute('CREATE TABLE calls(state TEXT,reserved REAL,cost REAL,input_tokens INTEGER,output_tokens INTEGER)')
        db.executemany('INSERT INTO calls VALUES(?,?,?,?,?)', [('done',2,1.5,10,20),('unknown',3,None,None,None)])
    count=[];connect=engine.connect
    def traced():
        count.append(1);return connect()
    engine.connect=traced
    @read_snapshot
    def read():
        one=engine.usage();two=engine.usage();return one,two
    one,two=read()
    assert one==two
    assert one['calls']==2 and one['estimated_cost_usd']==1.5
    assert one['reserved_unknown_usd']==3 and one['input_tokens']==10 and one['output_tokens']==20
    assert len(count)==1
    engine.usage();assert len(count)==2
