import html
import json
from datetime import datetime, timezone

import pytest

from app import discovery_sources as sources
from app.extraction import plain_text
from app.jev_requirements import spans


@pytest.mark.parametrize('encoding_layers', [0, 1, 2])
def test_encoded_job_html_keeps_requirements_separate(encoding_layers):
    bullets = ['Own product strategy and delivery with customers.' * 8,
               'Experience with Python or SQL; both are not required.' * 8,
               'Partner with engineering and design.' * 8]
    markup = '<strong>Responsibilities</strong><br><ul>' + ''.join(
        '<li>' + bullet + '</li>' for bullet in bullets) + '</ul>'
    for _ in range(encoding_layers):
        markup = html.escape(markup)
    result = plain_text(markup)
    assert result.splitlines() == ['Responsibilities'] + ['\u2022 ' + b for b in bullets]
    assert spans(result)
    assert plain_text(result) == result


def test_literal_comparison_is_not_removed():
    assert plain_text('Latency &lt; 100 ms &amp; accuracy &gt; 90%') == 'Latency < 100 ms & accuracy > 90%'


@pytest.mark.parametrize('board', ['a16z', 'sequoia', 'lightspeed'])
def test_vc_window_is_seven_days_without_relabeling_old_jobs(monkeypatch, board):
    requests = []
    def fetch(client, url, body=None, headers=None):
        if body is None:
            return '{"csrfToken":"anonymous-test"}'
        requests.append(body)
        return json.dumps({'jobs': [{'jobId': 'test', 'title': 'Product Manager',
            'companyName': 'Example', 'locations': ['US'], 'url': 'https://example.com/job',
            'description': '<p>Build useful products.</p>', 'timeStamp': '2026-10-06T12:00:00+00:00'}]})
    monkeypatch.setattr(sources, 'fetch', fetch)
    rows, coverage, warnings = sources.collect(board)
    assert requests[0]['query']['postedSince'] == 'P7D'
    assert rows[0]['date_label'] == 'Board reports past 7 days'
    assert 'past-7-days' in coverage
    assert sources.listing_freshness(rows[0], datetime(2026, 10, 11, tzinfo=timezone.utc)) == 'older'
    assert not warnings
