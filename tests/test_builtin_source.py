from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
import pytest
from app import discovery_sources as sources
from app import builtin_source


def page(job, next_page=None):
    return ('<input id="searchJobsInput"><button id="postedDateDropdownButton"></button>'
            f'<a href="/job/product-manager/{job}">PM</a><div id="pagination">'
            + (f'<a href="/jobs/product-management?page={next_page}">Next</a>' if next_page else '')
            + '</div>')


def test_filtered_pagination_preserves_filters_dates_and_deduplicates(monkeypatch):
    requests = []
    def fetch(client, url):
        q = parse_qs(urlsplit(url).query); requests.append(q)
        assert urlsplit(url).path == '/jobs/product-management'
        assert q['country'] == ['USA'] and q['daysSinceUpdated'] == ['1']
        assert q['search'] == ['product manager'] and q['allLocations'] == ['true']
        return page('1', 2) if 'page' not in q else page('2') + '<a href="/job/product-manager/1">repeat</a>'
    monkeypatch.setattr(sources, 'fetch', fetch)
    monkeypatch.setattr(sources, 'detail', lambda *a: {'title': 'Product Manager', 'posted_at': '2026-09-01T00:00:00Z'})
    rows, coverage, warnings = sources.collect('builtin', countries=['US'])
    assert len(requests) == 2 and len(rows) == 2 and not warnings
    assert 'All returned search pages checked' in coverage
    assert sources.listing_freshness(rows[0], datetime(2026, 10, 18, tzinfo=timezone.utc)) == 'older'


def test_limit_is_reported(monkeypatch):
    monkeypatch.setattr(builtin_source, 'MAX_PAGES', 1)
    monkeypatch.setattr(sources, 'fetch', lambda *a: page('1', 2))
    monkeypatch.setattr(sources, 'detail', lambda *a: {})
    rows, coverage, warnings = sources.collect('builtin', countries=['US'])
    assert len(rows) == 1 and 'Partial' in coverage
    assert any('page safety limit' in w for w in warnings)


def test_challenge_not_treated_as_empty_success(monkeypatch):
    monkeypatch.setattr(sources, 'fetch', lambda *a: '<html>Sign in</html>')
    with pytest.raises(ValueError, match='could not be verified'):
        sources.collect('builtin', countries=['US'])


def test_repeated_pages_report_partial(monkeypatch):
    monkeypatch.setattr(sources, 'fetch', lambda *a: page('1', 2))
    monkeypatch.setattr(sources, 'detail', lambda *a: {})
    rows, coverage, warnings = sources.collect('builtin', countries=['US'])
    assert len(rows) == 1 and any('repeated' in w for w in warnings)


def test_detail_failure_reports_partial(monkeypatch):
    monkeypatch.setattr(sources, 'fetch', lambda *a: page('1'))
    def fail(*a): raise ValueError('Missing JobPosting')
    monkeypatch.setattr(sources, 'detail', fail)
    rows, coverage, warnings = sources.collect('builtin', countries=['US'])
    assert not rows and warnings and 'Partial' in coverage
