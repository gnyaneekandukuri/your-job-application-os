from types import SimpleNamespace
import json
from app import resume_jd_review as review


class Engine:
    def __init__(self): self.data = {}
    def get(self, key): return self.data.get(key)
    def put(self, key, value): self.data[key] = value


class Client:
    def __init__(self, statuses): self.statuses, self.calls = statuses, []
    def evaluate(self, job_id, stage, state, questions):
        self.calls.append((state, questions))
        result = {}
        for i, status in enumerate(self.statuses):
            result[f'fit{i}'] = {'choice': status, 'confidence': .95}
            result[f'evidence{i}'] = {'choice': 'b1' if status in ('clear', 'partial') else 'none', 'confidence': .95}
        return result


def fixture(monkeypatch, categories):
    monkeypatch.setattr(review, 'extract', lambda *a, **kw: {'result': {'items': [
        {'quote': f'Requirement {i}', 'category': c} for i, c in enumerate(categories)]}})
    job = SimpleNamespace(id='j', title='PM', company='Example', description='Actual JD')
    record = {'draft': {'blocks': [{'text': 'Actual saved resume', 'citations': ['SECRET'], 'reason': 'SECRET'}]},
              'context': {'stories': 'SECRET'}, 'checks': 'SECRET'}
    return Engine(), job, record


def test_only_visible_text_and_weighted_score(monkeypatch):
    engine, job, record = fixture(monkeypatch, ['requirement', 'responsibility', 'preference'])
    client = Client(['clear', 'partial', 'missing'])
    result = review.evaluate(engine, job, record, client)
    assert result['score'] == 60
    assert 'SECRET' not in json.dumps(client.calls)
    assert set(client.calls[0][0]) == {'job_description', 'resume', 'criteria'}
    assert review.evaluate(engine, job, record, client) == result
    assert len(client.calls) == 1
    old_key = review.cache_key(job, record)
    record['draft']['blocks'][0]['text'] = 'Candidate edited resume'
    assert review.cache_key(job, record) != old_key
    assert engine.get(review.cache_key(job, record)) is None
    job.description = 'Changed JD'
    assert engine.get(review.cache_key(job, record)) is None


def test_uncertainty_not_zero_or_definitive_score(monkeypatch):
    engine, job, record = fixture(monkeypatch, ['requirement', 'responsibility'])
    result = review.evaluate(engine, job, record, Client(['clear', 'uncertain']))
    assert result['score'] is None
    assert (result['low'], result['high']) == (50, 100)


def test_ambiguous_requirements_and_empty_jd_not_false_perfect_score(monkeypatch):
    engine, job, record = fixture(monkeypatch, ['unclear'])
    result = review.evaluate(engine, job, record, Client(['clear']))
    assert result['score'] is None and result['low'] is None
    engine, job, record = fixture(monkeypatch, [])
    result = review.evaluate(engine, job, record, Client([]))
    assert result['score'] is None


def test_citation_selection_does_not_override_match(monkeypatch):
    engine, job, record = fixture(monkeypatch, ['requirement'])
    client = Client(['clear'])
    original = client.evaluate
    def no_evidence(*args):
        result = original(*args)
        result['evidence0'] = {'choice': 'none', 'confidence': .95}
        return result
    client.evaluate = no_evidence
    result = review.evaluate(engine, job, record, client)
    assert result['score'] == 100
    assert result['rows'][0]['status'] == 'clear'
    assert result['rows'][0]['resume_quote'] == ''


def test_low_citation_confidence_keeps_confident_match(monkeypatch):
    engine, job, record = fixture(monkeypatch, ['requirement', 'responsibility'])
    client = Client(['clear', 'clear'])
    original = client.evaluate
    def answers(*args):
        result = original(*args)
        result['evidence0']['confidence'] = .38
        result['fit0']['confidence'] = .93
        result['fit1']['confidence'] = .55
        return result
    client.evaluate = answers
    result = review.evaluate(engine, job, record, client)
    assert result['rows'][0]['status'] == 'clear'
    assert result['rows'][0]['resume_quote'] == ''
    assert all(not k.startswith('evidence') for k in client.calls[0][1])
    assert result['rows'][1]['status'] == 'uncertain'
    assert (result['low'], result['high']) == (50, 100)


def test_section_headings_excluded_even_from_cached_extraction(monkeypatch):
    engine, job, record = fixture(monkeypatch, [])
    monkeypatch.setattr(review, 'extract', lambda *a, **kw: {'result': {'items': [
        {'quote': 'What You’ll Be Doing:', 'category': 'unclear'},
        {'quote': 'Who We’re Looking For:', 'category': 'requirement'},
        {'quote': 'Own product strategy', 'category': 'responsibility'}]}})
    result = review.evaluate(engine, job, record, Client(['clear']))
    assert len(result['rows']) == 1
    assert result['score'] == 100
    assert not review.is_section_heading('Own these customer workflows:')
