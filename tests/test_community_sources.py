import json
from datetime import datetime, timezone
from threading import Event

import pytest

from app.community_sources import cosign_rows, hn_listing, lenny_rows, collect_hn
from app.source_leads import parse_leads, refresh, view

NOW = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)


def test_posting_freshness_requires_a_posting_date_not_an_update_or_comment():
    from app.discovery_sources import listing_freshness
    for basis in ('hiring_comment_timestamp', 'unknown', 'source_updated_at'):
        assert listing_freshness({'posted_at': NOW.isoformat(), 'date_basis':basis}, NOW) == 'unknown'
    assert listing_freshness({'posted_at':NOW.isoformat(), 'date_basis':'board_timestamp'}, NOW) == 'within_24h'
    assert listing_freshness({'posted_at':'2026-10-04', 'date_basis':'source_datePosted'}, NOW) == 'date_only'


def test_hn_comment_date_not_thread_date_and_no_replies():
    comment = {'id': 12, 'type': 'comment', 'parent': 10, 'time': NOW.timestamp()-120,
               'text': 'Example | New York, US | Full time<p>Senior Product Manager</p><p>Build our AI platform.</p>'}
    row = hn_listing(comment, 10, NOW)
    assert row['company'] == 'Example'
    assert row['title'] == 'Senior Product Manager'
    assert row['date_basis'] == 'hiring_comment_timestamp'
    assert row['url'].endswith('id=12')
    for change in ({'parent': 12}, {'deleted': True}, {'dead': True},
                   {'time': NOW.timestamp()-86401}, {'time': NOW.timestamp()+1}):
        assert hn_listing({**comment, **change}, 10, NOW) is None


def test_hn_does_not_invent_pm_job_from_company_prose():
    comment = {'id': 12, 'type': 'comment', 'parent': 10, 'time': NOW.timestamp(),
               'text': 'Example | Boston, US<p>Software Engineer</p><p>You will collaborate with our product managers.</p>'}
    assert hn_listing(comment, 10, NOW) is None


def test_hn_fetches_top_level_comments_not_replies():
    responses = {'user/whoishiring.json': {'submitted': [10]},
                 'item/10.json': {'id': 10, 'by': 'whoishiring', 'type': 'story',
                                 'title': 'Ask HN: Who is hiring? (October 2026)', 'kids': [12]},
                 'item/12.json': {'id': 12, 'parent': 10, 'type': 'comment', 'time': NOW.timestamp(),
                                 'kids': [14], 'text': 'Example | NYC, US | Product Manager'}}
    rows, coverage, warnings = collect_hn(None, lambda client, url: json.dumps(responses[url.split('/v0/')[1]]), NOW)
    assert len(rows) == 1 and not warnings
    assert 'Partial' in coverage


def cosign_page(jobs):
    data = ['$', 'component', None, {'snapshot': {'search': {'page': {'page': jobs, 'isDone': False}}}}]
    return '<script>self.__next_f.push(' + json.dumps([1, '5:' + json.dumps(data) + '\n']) + ')</script>'


def test_cosign_flight_references_not_descriptions_and_closed_excluded():
    job = {'postingId': '1', 'title': 'Product Manager', 'organizationName': 'Example',
           'location': 'USA', 'url': 'https://example.com/job', 'description': '$1e',
           'postedAt': NOW.timestamp()*1000}
    rows = cosign_rows(cosign_page([job, {**job, 'closed': True}]))
    assert len(rows) == 1 and rows[0]['description'] == ''
    assert rows[0]['posted_at'] == NOW.isoformat()
    with pytest.raises(ValueError):
        cosign_rows('<html>Sign in</html>')


def test_lenny_update_date_is_not_a_posting_date():
    rows = lenny_rows({'results': [{'hits': [{'job_id': '1', 'title': 'Product Manager',
            'company_name': 'Example', 'url': 'https://example.com/job?utm_source=lenny',
            'updated_at': NOW.isoformat()}]}]})
    assert rows[0]['posted_at'] is None
    assert rows[0]['source_updated_at'] == NOW.isoformat()
    assert rows[0]['url'] == 'https://example.com/job'
    with pytest.raises(ValueError):
        lenny_rows({'error': 'login required'})


def test_company_and_newsletter_leads_never_become_job_postings():
    breakout = '<script type="application/ld+json">' + json.dumps({'@graph': [
        {'@type': 'ItemList', 'itemListElement': [{'item': {'@type': 'Organization',
            'name': 'Example', 'url': 'https://example.com'}}]}]}) + '</script>'
    assert parse_leads('breakout', breakout)[0]['title'] == 'Example'
    ramp = '<a href="/data/top-saas-vendors-on-ramp-sept-2026">September</a><a href="https://evil.example/data/top-saas-vendors-on-ramp-x">Bad</a>'
    assert len(parse_leads('ramp', ramp)) == 1
    feed = '<rss><channel><item><title>Roles to explore</title><link>https://example.com/post</link><pubDate>Sun, 04 Oct 2026 15:00:00 GMT</pubDate></item></channel></rss>'
    row = parse_leads('nextplay', feed)[0]
    assert 'published_at' in row and 'posted_at' not in row and 'description' not in row


def test_lead_failure_retains_previous_results_and_is_visible(tmp_path):
    def first(client, url):
        if url.endswith('/feed'):
            return '<rss><channel><item><title>Issue</title><link>https://example.com/post</link></item></channel></rss>'
        raise ValueError('Changed')
    refresh(tmp_path, Event(), first)
    def failed(client, url):
        raise ValueError('Changed again')
    refresh(tmp_path, Event(), failed)
    source = next(s for s in view(tmp_path) if s['id'] == 'nextplay')
    assert source['state'] == 'error' and source['error']
    assert source['last_success'] and source['items'][0]['title'] == 'Issue'
