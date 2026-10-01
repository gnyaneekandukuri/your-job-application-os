from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
import pytest
from app.linkedin_source import Cards, age_bounds, collect_linkedin
from app.discovery_sources import listing_freshness

CARD = '''<div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:123">
<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/product-manager-at-example-123?trackingId=private"></a>
<img src="image"><div class="base-search-card__info"><h3 class="base-search-card__title">Product Manager</h3>
<h4 class="base-search-card__subtitle"><a>Example &amp; Co</a></h4><div><span class="job-search-card__location">San Francisco, CA</span>
<time datetime="2026-10-01">2 hours ago</time></div></div></div>'''


def test_cards_preserve_dates_and_strip_tracking():
    parser = Cards(); parser.feed(CARD + CARD.replace('123','456'))
    assert len(parser.rows) == 2
    assert parser.rows[0]['company'] == 'Example & Co'
    assert parser.rows[0]['posted_at'] == '2026-10-01'
    assert parser.rows[0]['date_label'] == '2 hours ago'
    assert '?' not in parser.rows[0]['url']
    assert parser.rows[1]['source_id'] == '456'


def test_rounded_age_expires_without_claiming_an_exact_date():
    now = datetime(2026,10,1,20,tzinfo=timezone.utc)
    item = {'date_basis':'source_relative_age', **age_bounds('2 hours ago',now)}
    assert listing_freshness(item,now) == 'within_24h'
    assert listing_freshness(item,now+timedelta(hours=21,minutes=1)) == 'unknown'
    assert listing_freshness(item,now+timedelta(hours=22)) == 'older'
    assert listing_freshness({'date_basis':'source_relative_age',**age_bounds('1 day ago',now)},now) == 'older'
    assert listing_freshness({'date_basis':'source_relative_age',**age_bounds('23 hours ago',now)},now) == 'unknown'
    assert age_bounds('Reposted recently',now) == {}
    assert listing_freshness(item,now-timedelta(seconds=1)) == 'unknown'


def test_country_and_past_day_filter_required_and_duplicates_removed():
    calls=[]
    def fetch(client,url):
        calls.append(url)
        return 'Past 24 hours filter is currently applied' + CARD + CARD
    rows,coverage,warnings = collect_linkedin(None,fetch,['US'])
    assert len(rows) == 1
    query=parse_qs(urlsplit(calls[0]).query)
    assert query['location'] == ['United States']
    assert query['f_TPR'] == ['r86400']
    assert 'Partial coverage' in coverage
    assert rows[0]['search_country'] == 'US'
    assert rows[0]['description'] == ''
    older,_,_ = collect_linkedin(None,lambda *args:'Past 24 hours filter is currently applied' + CARD.replace('2 hours ago','1 day ago'),['US'])
    assert older == []
    with pytest.raises(ValueError,match='could not be verified'):
        collect_linkedin(None,lambda *args:'Sign in', ['US'])
    with pytest.raises(ValueError,match='country'):
        collect_linkedin(None,fetch,[])


def test_linkedin_scan_route_is_guarded_and_isolated(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        calls=[]
        d.start=lambda boards:calls.append(boards) or {'running':True}
        assert client.post('/api/discovery/linkedin/scan').status_code == 403
        assert client.post('/api/discovery/linkedin/scan',headers={'X-Local-Review':'1'}).status_code == 200
        assert calls == [['linkedin']]


def test_unknown_rescan_age_does_not_keep_old_freshness(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/setup',headers={'X-Local-Review':'1'},json={'target_roles':['Product Manager'],'countries':['US']})
        d=client.app.state.discovery
        parser=Cards();parser.feed(CARD)
        row={**parser.rows[0], 'location':'Chicago, IL', 'description':'', 'date_basis':'source_relative_age',
             'search_country':'US','source_filter':'past_24_hours'}
        d.save('linkedin',{**row,**age_bounds('2 hours ago',datetime.now(timezone.utc))})
        item=d.view()['items'][0]
        assert item['freshness']=='within_24h'
        assert item['location_match']['status']=='match'
        d.save('linkedin',{**row,'date_label':'Recently'})
        assert d.view()['items'][0]['freshness']=='unknown'
        d.save('linkedin',{**row,'location':'Toronto, Canada'})
        assert d.view()['items'][0]['location_match']['status']=='outside'
