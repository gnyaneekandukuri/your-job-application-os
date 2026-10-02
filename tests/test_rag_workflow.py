"""Cost/coverage regressions, fully local and independent of provider availability."""
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from app.analysis_context import retrieve_evidence, MAX_BYTES
from app.job_requirements import extract, source_hash, ExtractRequirements, SavedRequirement, shared_key
from app.retrieval import RetrievalError
from app.role_analysis import RoleAnalysis


def test_search_does_not_silently_starve_a_requirement():
    passages = [{'id': 'a', 'text': 'alpha ' * 4500, 'raw_vector': [1., 0.]},
                {'id': 'b', 'text': 'beta ' * 5500, 'raw_vector': [0., 1.]}]
    with pytest.raises(RetrievalError, match='strongest evidence'):
        retrieve_evidence(passages, ['alpha', 'beta'], [[1., 0.], [0., 1.]])


def test_search_trace_maps_each_query_to_original_candidates():
    passages = [{'id': str(i), 'text': f'Original account {i}', 'raw_vector': [1., float(i)],
                 'context': 'Generated indexing note', 'source_sha256': 'revision'} for i in range(140)]
    result = retrieve_evidence(passages, ['account 1', 'account 2'], [[1., 1.], [1., 2.]])
    assert result['trace']['candidate_count'] == 140
    assert result['trace']['selected_bytes'] <= MAX_BYTES
    for query in result['trace']['queries']:
        assert query['candidate_ids'][0] in query['selected_ids']
    assert all('context' not in p and 'raw_vector' not in p for p in result['passages'])
    assert {p['text'] for p in result['passages']} <= {p['text'] for p in passages}


def test_large_library_is_searched_locally_not_rejected_by_old_prompt_limit():
    chunks = [{'id': str(i), 'text': 'Original story. '*200} for i in range(130)]
    index = {'chunks': [{**c, 'raw_vector': [1., 0.]} for c in chunks]}
    entry = {'id': 'source', 'source_class': 'RAW_EVIDENCE', 'title': 'Story', 'path': 'story.md', 'employer': 'Example'}
    library = SimpleNamespace(entries=lambda: [entry], service=lambda _: SimpleNamespace(snapshot=lambda: ('text','hash',chunks,'fingerprint')))
    manager = RoleAnalysis(SimpleNamespace(get=lambda _: index, path=Path('unused/db')), library)
    assert len(manager.passages()) == 130


def fake_engine():
    cache, calls = {}, []
    result = {'items': [{'category': 'preference', 'interpretation': 'Python or SQL is preferred, not required.',
                         'quote': 'Python or SQL is preferred, not required.', 'explanation': 'Explicitly optional alternatives.'}],
              'warnings': [], 'empty_reason': ''}
    def paid(kind, payload):
        calls.append(payload)
        return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]}
    return SimpleNamespace(lock=RLock(), get=cache.get, put=lambda k,v: cache.update({k:v}),
                           usage=lambda: {}, paid=paid), calls, cache


def run_extraction(engine, job):
    return extract(engine, job, ExtractRequirements(consent_to_send=True, source_sha256=source_hash(job)))


def test_identical_jd_reuses_successful_interpretation_without_crossing_job_identity():
    engine, calls, _ = fake_engine()
    a = SimpleNamespace(id='board-a', description='Python or SQL is preferred, not required.')
    b = SimpleNamespace(id='board-b', description=a.description)
    first, second = run_extraction(engine, a), run_extraction(engine, b)
    assert len(calls) == 1
    assert second['cached'] is True
    assert first['result']['job_id'] == 'board-a'
    assert second['result']['job_id'] == 'board-b'
    assert second['result']['items'][0]['category'] == 'preference'
    b.description += '\nWork with customers.'
    run_extraction(engine, b)
    assert len(calls) == 2


def test_invalid_extraction_never_enters_shared_cache():
    engine, calls, cache = fake_engine()
    job = SimpleNamespace(id='a', description='A different description without the quoted words.')
    with pytest.raises(RetrievalError, match='exactly match'):
        run_extraction(engine, job)
    assert shared_key(job) not in cache
    with pytest.raises(RetrievalError):
        run_extraction(engine, job)
    assert len(calls) == 1  # Retain failed response; no automatic paid retry.


@pytest.mark.parametrize('invent_quote', [False, True])
def test_real_batch_sends_only_retrieved_originals_and_checks_citations(invent_quote):
    from app.dimension_score import DIMENSIONS
    cache, calls = {}, []
    passages = [{'id': str(i), 'text': f'I owned the original product launch number {i}.',
                 'raw_vector': [1., float(i)], 'source_sha256': 'revision', 'heading': 'Launch',
                 'start_line': 1, 'end_line': 1, 'employer': 'Example', 'source_title': 'Story',
                 'source_path': 'story.md', 'source_class': 'RAW_EVIDENCE'} for i in range(40)]
    def paid(kind, payload):
        body = json.loads(payload['input'])
        calls.append(body)
        assert len(body['passages']) <= 4
        assert len(json.dumps(body['passages']).encode()) <= MAX_BYTES
        assert all('raw_vector' not in p for p in body['passages'])
        p = body['passages'][0]
        assessment = dict(alignment='strong', relationship='similar_work', confidence='medium',
                          **{k:'Supported by the original account.' for k in ('confidence_reason','ownership','scope','outcomes','transfer_reason','recency_reason')},
                          dimensions={k:{'anchor':'demonstrated','reason':'Original launch ownership.'} for k in DIMENSIONS})
        result = {'results': [{'requirement_id':'r1', 'judgment': {'support':'direct', 'explanation':'Launch ownership.',
                  'limitation':'', 'clarification':'', 'fact_ids':[], 'assessment':assessment,
                  'evidence':[{'passage_id':p['id'], 'quote':'Invented unsupported launch outcome.' if invent_quote else p['text']}]}}]}
        return {'status':'completed', 'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}
    engine = SimpleNamespace(path=Path('unused/db'), get=cache.get, put=lambda k,v:cache.update({k:v}),
                             incremental_embeddings=lambda _: [[1., 1.]], paid=paid)
    manager = RoleAnalysis(engine, SimpleNamespace())
    req = SavedRequirement(id='r1', category='responsibility', interpretation='Own a launch.',
                           quote='Own a product launch.', explanation='Stated responsibility.')
    def run():
        return manager.batch([req], passages, {'facts':{'facts':[]}}, job_id='job')
    if invent_quote:
        with pytest.raises(RetrievalError, match='exact source quote'):
            run()
    else:
        rows = run()
        assert rows[0][2][0].quote in {p['text'] for p in passages}
        run()
        assert len(calls) == 1  # Successful assessment is reused.
    traces = [v for k,v in cache.items() if k.startswith('retrieval-selection:')]
    assert traces[0]['candidate_count'] == 40
    assert traces[0]['requirements'] == ['r1']
