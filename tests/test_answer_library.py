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


def test_product_introduction_and_nonlinear_are_general_questions():
    assert not specific('Describe a product introduction you led.', 'Acme')
    assert not specific('How do you approach nonlinear analysis or linear growth models?', 'Linear')
    assert specific('Why do you use Linear?', 'Linear') and specific('Introduce yourself to the founders.', 'Acme')


def test_answers_naming_their_employer_and_unreviewed_drafts_are_not_offered(tmp_path, monkeypatch):
    from app import application_answer
    with TestClient(create_app(tmp_path)) as client:
        engine = client.app.state.retrieval
        monkeypatch.setattr(engine, 'embed', fake_embed)
        acme, other, current = job(client, 'Acme'), job(client, 'Beta'), job(client, 'Earnix')
        answer(client, acme, 'Tell us about yourself and your AI tools.', 'I use AI tools daily and I want to join Acme.')
        route = f'/api/jobs/{current}/application-answers/matches'
        assert client.post(route, headers=H, json={'question': 'Which AI tools do you use?'}).json()['matches'] == []
        # A generated draft is stored unreviewed: not offered until you save or copy it.
        job_obj = client.app.state.service.review(other).job
        draft = application_answer.AnswerInput(id=uuid4(), question='What AI tools do you use?')
        application_answer.persist(engine, job_obj, draft, {'text': 'Claude daily.', 'questions': [], 'evidence': [], 'reviewed': False})
        assert client.post(route, headers=H, json={'question': 'Which AI tools do you use?'}).json()['matches'] == []
        row = client.get(f'/api/jobs/{other}/application-answers').json()['items'][0]
        answer(client, other, row['question'], row['text'], id=row['id'], revision=row['revision'])
        assert [m['company'] for m in client.post(route, headers=H, json={'question': 'Which AI tools do you use?'}).json()['matches']] == ['Beta']


def test_personal_library_save_edit_and_reuse_without_drafting(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        engine = client.app.state.retrieval
        def forbidden(*args, **kwargs):
            raise AssertionError('Manual library must not call an AI provider')
        monkeypatch.setattr(engine, 'embed', forbidden)
        monkeypatch.setattr(engine, 'paid', forbidden)
        question = "What's something you do today with AI that would have taken you 10x longer a year ago? Walk us through it."
        text = 'I built my own job search app.\n\nI review the results myself.'
        body = {'id':str(uuid4()), 'question':question, 'text':text, 'revision':''}
        response = client.post('/api/answer-library/save', headers=H, json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved['text'] == text and saved['reviewed'] and saved['evidence'] == []
        assert client.get('/api/answer-library').json()['items'][0]['text'] == text
        assert client.post('/api/answer-library/save', headers=H, json=body).status_code == 409
        body.update(revision=saved['revision'], text=text+' My own edit.')
        saved = client.post('/api/answer-library/save', headers=H, json=body).json()
        from app.answer_library import entries
        library = entries(engine, lambda _: '')
        assert len(library)==1 and library[0]['text']==body['text']
        # Reusing the saved text for a job also makes no AI call.
        jid = job(client, 'Example')
        reused = answer(client, jid, question, body['text'], reused_from='personal-library:'+saved['id'])
        assert reused['text']==body['text'] and reused['evidence']==[]
        body.update(revision=saved['revision'], reusable=False)
        assert client.post('/api/answer-library/save', headers=H, json=body).status_code==200
        assert all(e['job_id']!='personal-library' for e in entries(engine, lambda _: ''))


def test_personal_library_rejects_blank_answer(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/answer-library/save', headers=H,
            json={'id':str(uuid4()), 'question':'Question?', 'text':'  '})
        assert response.status_code==422


def test_library_lookup_is_free_and_finds_saved_question(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        engine=client.app.state.retrieval
        def forbidden(*args, **kwargs): raise AssertionError('Lookup must be free')
        monkeypatch.setattr(engine,'embed',forbidden)
        monkeypatch.setattr(engine,'paid',forbidden)
        q="What's something you do today with AI that would have taken you 10x longer a year ago? Walk us through it."
        body={'id':str(uuid4()),'question':q,'text':'My original answer, unchanged.'}
        assert client.post('/api/answer-library/save',headers=H,json=body).status_code==200
        def lookup(question):
            r=client.post('/api/answer-library/matches',headers=H,json={'question':question})
            assert r.status_code==200
            return r.json()['matches']
        found=lookup(q)
        assert found[0]['exact'] and found[0]['text']==body['text']
        assert lookup('What work do you do with AI today that took 10x longer a year ago?')
        assert lookup('Describe your Kubernetes cluster incident response')==[]
        body.update(revision=client.get('/api/answer-library').json()['items'][0]['revision'],reusable=False)
        client.post('/api/answer-library/save',headers=H,json=body)
        assert lookup(q)==[]


def test_library_answers_to_why_questions_stay_reusable(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        q = 'Why are you interested in product management roles?'
        body = {'id':str(uuid4()), 'question':q, 'text':'I like turning customer problems into this role\'s roadmap.'}
        assert client.post('/api/answer-library/save', headers=H, json=body).status_code == 200
        found = client.post('/api/answer-library/matches', headers=H, json={'question':q}).json()['matches']
        assert found and found[0]['exact']
        # The same question saved for a specific job stays with that job.
        jid = job(client, 'Example')
        answer(client, jid, 'Why are you interested in this role?', 'Because of Example.')
        from app.answer_library import entries
        assert [e['job_id'] for e in entries(client.app.state.retrieval, lambda _: 'Example')] == ['personal-library']
