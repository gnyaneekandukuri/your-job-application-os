import json
from threading import Event

import httpx
import pytest

from app.discovery_jev import JevClassifier, POLICY


def response(function='product', confidence=.95):
    policy = json.loads(POLICY.read_text())
    answers = {}
    for name, selected in [('function', function), ('focus', 'ai_ml')]:
        answers[name] = {'type': 'choice', 'choice': selected, 'confidence': confidence,
                         'probabilities': {key: float(key == selected) for key in policy['questions'][name]['criteria']}}
    return {'model': policy['model'], 'answers': answers, 'usage': {'input_tokens': 1000}}


@pytest.fixture
def item():
    return {'id': 'one', 'title': 'Product Manager', 'description': 'Own evaluations and orchestration for autonomous workflows. ' * 8,
            'location_match': {'status': 'match'}, 'private_story': 'MUST NOT SEND', 'candidate': 'MUST NOT SEND'}


def setup(tmp_path, monkeypatch, payload=None):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-secret')
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json=payload or response(), request=httpx.Request('POST', url))
    return JevClassifier(tmp_path/'discovery.sqlite3', post), calls


def test_cache_reuse_and_no_private_context(tmp_path, monkeypatch, item):
    classifier, calls = setup(tmp_path, monkeypatch)
    first = classifier.classify(item)
    assert first['labels']['function'] == 'Product management'
    assert first['estimated_cost_usd'] == pytest.approx(.000042)
    assert classifier.classify({**item, 'id': 'duplicate'})['cached']
    classifier.annotate([item])
    assert len(calls) == 1
    assert set(calls[0]['json']['state']) == {'title', 'description'}
    assert 'MUST NOT SEND' not in json.dumps(calls[0]['json'])
    classifier.classify({**item, 'description': item['description']+'New duties.'})
    assert len(calls) == 2


def test_low_confidence_is_unclear_and_does_not_change_priority(tmp_path, monkeypatch, item):
    classifier, _ = setup(tmp_path, monkeypatch, response('gtm', .3))
    item['keyword_match'] = {'priority': 'high'}
    classifier.classify(item)
    classifier.annotate([item])
    assert item['role_classification']['labels']['function'] == 'Unclear'
    assert item['keyword_match']['priority'] == 'high'


@pytest.mark.parametrize('bad', ['unknown_choice', 'probabilities', 'confidence', 'model'])
def test_invalid_results_are_not_cached(tmp_path, monkeypatch, item, bad):
    value = response()
    if bad == 'unknown_choice': value['answers']['function']['choice'] = 'CEO'
    if bad == 'probabilities': value['answers']['function']['probabilities']['product'] = float('nan')
    if bad == 'confidence': value['answers']['function']['confidence'] = 2
    if bad == 'model': value['model'] = 'unexpected'
    # Stub response avoids JSON encoder rejecting NaN before our validator sees it.
    classifier, _ = setup(tmp_path, monkeypatch)
    class Stub:
        def raise_for_status(self): pass
        def json(self): return value
    classifier.post = lambda *a, **k: Stub()
    assert classifier.classify(item)['state'] == 'error'
    classifier.annotate([item])
    assert item['role_classification'] is None


def test_no_key_missing_jd_and_outside_location_make_no_calls(tmp_path, monkeypatch, item):
    classifier, calls = setup(tmp_path, monkeypatch)
    assert classifier.classify({**item, 'description': ''})['state'] == 'needs_description'
    result = classifier.run([{**item, 'location_match': {'status': 'outside'}}], Event())
    assert result['skipped'] == 1
    monkeypatch.delenv('TYPESAFE_API_KEY')
    assert classifier.run([item], Event())['state'] == 'not_configured'
    assert not calls


def test_error_breaks_scan_without_exposing_key_or_body(tmp_path, monkeypatch, item):
    classifier, calls = setup(tmp_path, monkeypatch)
    def failed(url, **kwargs):
        calls.append(1)
        return httpx.Response(429, text='test-secret provider details', request=httpx.Request('POST', url))
    classifier.post = failed
    result = classifier.run([item, {**item, 'title': 'Another role'}], Event())
    assert result['state'] == 'error'
    assert len(calls) == 1
    assert 'test-secret' not in json.dumps(result)


def test_policy_change_invalidates_cache(tmp_path, monkeypatch, item):
    classifier, calls = setup(tmp_path, monkeypatch)
    classifier.classify(item)
    policy = json.loads(POLICY.read_text())
    policy['version'] = 'next'
    target = tmp_path/'policy.json'
    target.write_text(json.dumps(policy))
    monkeypatch.setattr('app.discovery_jev.POLICY', target)
    classifier.annotate([item])
    assert item['role_classification'] is None
    classifier.classify(item)
    assert len(calls) == 2
