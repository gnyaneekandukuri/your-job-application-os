from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.answer_library import specific

H = {'X-Local-Review': '1'}
# Toy meaning vectors: questions about the same topic point the same way.
TOPICS = {'ai tools': [1, 0, 0], 'mentor': [0, 1, 0], 'kubernetes': [0, 0, 1]}


def fake_embed(texts):
    out = []
    for t in texts:
        v = next((vec for word, vec in TOPICS.items() if word in t.lower()), [0.6, 0.0, 0.8])
        out.append(v)
    return out


@pytest.mark.parametrize('question,company', [
    ('Why do you want to work at DOSS.com?', 'DOSS'), ('Why Levelpath?', 'Levelpath'),
    ('Why would you like to join Gravitee? What could your experience bring to the role?', 'Gravitee'),
    ('Write a short introduction for the founders.', 'Acme'), ('What excites you about this role?', 'Acme'),
    ('How would you improve Acme for small businesses?', 'Acme Inc'),
])
def test_company_specific_questions(question, company):
    assert specific(question, company)


@pytest.mark.parametrize('question', [
    'What AI tools are you currently using today and how are you using them?', 'Describe your mentorship skills.',
    "What's the most impactful thing you've built? What was your specific contribution?",
    'Do you want to tell us anything else about yourself?'])
def test_general_questions(question):
    assert not specific(question, 'Acme')


def job(client, company):
    r = client.post('/api/jobs', headers=H, json={'request_id': str(uuid4()), 'company': company, 'title': 'PM',
                                                 'description': 'Build AI products with engineering teams.'})
    data = r.json()
    return data.get('job', data)['id']


def answer(client, job_id, question, text, **extra):
    body = {'id': str(uuid4()), 'revision': '', 'question': question, 'note': '', 'limit': None, 'unit': 'words', 'text': text, **extra}
    r = client.post(f'/api/jobs/{job_id}/application-answers/save', headers=H, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_matches_saved_general_answers_from_other_jobs_only(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        engine = client.app.state.retrieval
        monkeypatch.setattr(engine, 'embed', fake_embed)
        monkeypatch.setattr(engine, 'paid', lambda *a, **k: pytest.fail('no generation call'))
        first, second, current = job(client, 'Datadog'), job(client, 'Gravitee'), job(client, 'Earnix')
        old = answer(client, first, 'What AI tools are you currently using?', 'Claude and Cursor, daily.')
        answer(client, first, 'Describe your mentorship skills.', 'I coached two APMs.', reusable=False)
        answer(client, second, 'Why would you like to join Gravitee?', 'Because of APIs.')
        route = f'/api/jobs/{current}/application-answers/matches'
        found = client.post(route, headers=H, json={'question': 'Which AI tools do you use day to day?', 'limit': 3}).json()
        assert [m['company'] for m in found['matches']] == ['Datadog']
        match = found['matches'][0]
        assert match['text'] == 'Claude and Cursor, daily.' and match['over_limit'] and match['score'] >= 0.55
        # Opted-out and company-specific answers are never offered; unrelated topics fall below the cutoff.
        assert client.post(route, headers=H, json={'question': 'How do you mentor junior PMs?'}).json()['matches'] == []
        assert client.post(route, headers=H, json={'question': 'Kubernetes cost work?'}).json()['matches'] == []
        assert client.post(route, headers=H, json={'question': 'Why Earnix?'}).json() == {'specific': True, 'matches': []}
        # The edited answer is what gets offered, and reusing it keeps its evidence.
        edited = answer(client, first, old['question'], 'Claude, Cursor and v0, daily.', id=old['id'], revision=old['revision'])
        match = client.post(route, headers=H, json={'question': 'Which AI tools do you use?'}).json()['matches'][0]
        assert match['text'] == 'Claude, Cursor and v0, daily.'
        copied = answer(client, current, 'Which AI tools do you use?', match['text'], reused_from=f"{first}:{edited['id']}")
        assert copied['reused_from'] == f"{first}:{edited['id']}"
