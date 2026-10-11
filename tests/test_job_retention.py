from datetime import datetime, timedelta, timezone
import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.job_retention import age_state, cleanup

NOW = datetime(2026, 10, 10, 20, tzinfo=timezone.utc)

def listing(n, days=15, **changes):
    return dict(source_id=str(n), title='Product Manager', company='Example',
                url=f'https://example.com/jobs/{n}', location='USA', description='Responsibility '*50,
                posted_at=(NOW-timedelta(days=days)).isoformat(), date_basis='board_timestamp', **changes)


def test_age_uses_publication_not_discovery():
    assert age_state(listing(1, 3), NOW) == 'active'
    assert age_state(listing(1, 4), NOW) == 'older_than_four_days'
    for basis in ('unknown','hiring_comment_timestamp','source_updated_at'):
        item=listing(1);item['date_basis']=basis
        assert age_state(item,NOW)=='unknown'
    assert age_state({'posted_at':'2026-10-30T00:00:00Z'},NOW)=='unknown'
    assert age_state({'posted_at':'bad'},NOW)=='unknown'
    # Date-only ages allow timezone uncertainty; no early deletion.
    assert age_state({'posted_at':'2026-10-06'},NOW)=='active'


def test_cleanup_protects_work_and_remembers_deleted_jobs(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        ids=[d.save('yc',listing(n))[0] for n in range(6)]
        headers={'X-Local-Review':'1'}
        assert client.post(f'/api/discovery/{ids[1]}/keep',json={'keep':True},headers=headers).status_code==200
        with d.db() as db:
            db.execute('UPDATE opportunities SET job_id=? WHERE id=?',('saved-role',ids[2]))
            db.execute('INSERT INTO selected_assessments VALUES(?,?,?,?)',(ids[3],'hash','COMPLETE',''))
            item=listing(4);item['date_basis']='unknown'
            db.execute('UPDATE opportunities SET payload=? WHERE id=?',(json.dumps(item),ids[4]))
            db.execute('UPDATE opportunities SET payload=? WHERE id=?',(json.dumps(listing(5,13)),ids[5]))
        assert cleanup(d,NOW)['deleted']==1
        with d.db() as db:
            assert db.execute('SELECT count(*) FROM opportunities').fetchone()[0]==5
            assert db.execute('SELECT count(*) FROM revisions WHERE opportunity_id=?',(ids[0],)).fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM sightings WHERE opportunity_id=?',(ids[0],)).fetchone()[0]==0
        # Same vacancy across a new board and changed date never re-enters paid work.
        assert d.save('linkedin',listing(0,0))==(None,False)
        other=listing(0);other['url']='https://example.com/alias'
        assert d.save('yc',other)==(None,False)
        # A genuinely different opening at the same company remains discoverable.
        assert d.save('yc',listing('new',0))[1]
        assert cleanup(d,NOW)['deleted']==0


def test_watchlist_survives_cleanup_and_does_not_change_priority(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery;d.save('yc',listing(1))
        headers={'X-Local-Review':'1'}
        data={'company':'Example','careers_url':'https://example.com/careers','reason':'AI product focus','roles':'AI PM'}
        assert client.post('/api/discovery/watchlist',json=data,headers=headers).status_code==200
        cleanup(d,NOW)
        assert client.get('/api/discovery/watchlist').json()[0]['reason']=='AI product focus'
        oid,_=d.save('yc',listing(2,0))
        item=d.view(include_analysis=False)['items'][0]
        assert item['company_watched'] and item['id']==oid
        assert 'keyword_match' not in item
        assert client.post('/api/discovery/watchlist',json={**data,'remove':True},headers=headers).json()==[]
