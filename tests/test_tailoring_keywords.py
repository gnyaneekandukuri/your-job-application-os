import json

import pytest

from app.retrieval import RetrievalError
from app.tailoring import Action, Edit, KeywordCoverage, KeywordEdit, ProposalDecision, keyword_check, keyword_proposals
from tests.test_tailoring import generate, reviews, setup  # noqa: F401 (fixture)

KEYWORD_TEXT = 'Built a prototype of reporting tools for 12 users during a pilot.'


def keyword_review(provider, edits):
    """Make the fake keyword call return one suggested and one missing keyword."""
    request = provider.request
    def reviewing(endpoint, payload):
        response = request(endpoint, payload)
        if payload['text']['format']['name'] == 'resume_keywords':
            item = json.loads(payload['input'])['full_draft'][-1]['item_id']
            data = {'keywords': [
                {'keyword': 'reporting tools', 'jd_quote': 'Build reporting tools', 'priority': 'required', 'importance': 'important', 'status': 'suggested', 'reason': ''},
                {'keyword': 'Rust', 'jd_quote': 'Build reporting tools', 'priority': 'required', 'importance': 'important', 'status': 'missing', 'reason': 'No Rust work in the evidence.'}],
                'edits': [{'item_id': item, 'keywords': ['reporting tools'], 'question': '', 'reason': 'The pilot prototype was a reporting tool.',
                           'jd_quote': 'Build reporting tools', 'evidence_quote': 'reporting prototype', 'reads_naturally': True} | e for e in edits]}
            response['output'][0]['content'][0]['text'] = json.dumps(data)
        return response
    provider.request = reviewing


def test_important_keyword_is_placed_in_the_resume_and_yes_keeps_it(setup):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT}])
    r = generate(s, j)
    names = [c['text']['format']['name'] for c in p.calls]
    assert sorted(names) == ['resume_claim_check', 'resume_draft', 'resume_keywords']
    keywords = p.calls[names.index('resume_keywords')]
    assert 'Return keywords and edits' in keywords['instructions'] and 'Return keywords and edits' not in reviews(p.calls)[0]['instructions']
    card = next(x for x in r['proposals'] if x['operation'] == 'keyword')
    assert card['status'] == 'accepted' and card['automatic'] and card['inline'] and card['keywords'] == ['reporting tools']
    block = r['draft']['blocks'][-1]
    assert block['text'] == KEYWORD_TEXT and block['change_type'] == 'job_alignment'  # Proven, so kept without a click.
    assert card['before'][0]['text'] != KEYWORD_TEXT  # The original is kept for undo.
    assert [k['status'] for k in r['keywords']] == ['suggested', 'missing']
    undone = s.decide_proposal(j, ProposalDecision(revision=r['revision'], proposal_id=card['id'], decision='reject'))['record']
    assert undone['draft']['blocks'][-1] == card['before'][0]
    assert len(p.calls) == 3  # Undo is local.


def test_no_restores_the_original_point_exactly(setup):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT}])
    r = generate(s, j)
    card = next(x for x in r['proposals'] if x['operation'] == 'keyword')
    updated = s.decide_proposal(j, ProposalDecision(revision=r['revision'], proposal_id=card['id'], decision='reject'))['record']
    assert updated['draft']['blocks'][-1] == card['before'][0]


def test_no_never_overwrites_the_candidates_own_edit(setup):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT}])
    r = generate(s, j)
    card = next(x for x in r['proposals'] if x['operation'] == 'keyword')
    blocks = r['draft']['blocks']; blocks[-1]['text'] = 'My own wording for 12 users.'
    edited = s.edit(j, Edit(revision=r['revision'], blocks=blocks))['record']
    with pytest.raises(RetrievalError, match='You edited this point'):
        s.decide_proposal(j, ProposalDecision(revision=edited['revision'], proposal_id=card['id'], decision='reject'))


def test_recheck_keeps_decided_keyword_edits(setup):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT}])
    r = generate(s, j)
    s.recheck(j, Action(revision=r['revision'], confirmed=True)); s.worker.join(5)
    review = json.loads(reviews(p.calls)[-1]['input'])
    assert review['full_draft'][-1]['text'] == KEYWORD_TEXT  # An automatically kept edit is part of the resume.
    again = s.view(j)['record']
    assert [x['status'] for x in again['proposals'] if x['operation'] == 'keyword'] == ['accepted']  # Not suggested twice.


def test_a_yes_is_remembered_and_not_asked_again_on_the_next_job(setup):
    from types import SimpleNamespace
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT, 'question': 'Was the prototype a reporting tool?', 'evidence_quote': ''}])
    r = generate(s, j)
    card = next(x for x in r['proposals'] if x['operation'] == 'keyword')
    assert card['question'] and not card.get('inline')  # First job: asks.
    s.decide_proposal(j, ProposalDecision(revision=r['revision'], proposal_id=card['id'], decision='accept'))
    second = SimpleNamespace(id='job-2', title=j.title, company='Other Inc', description=j.description)
    later = generate(s, second)
    again = next(x for x in later['proposals'] if x['operation'] == 'keyword')
    assert again['question'] == '' and again['inline']  # Second job: placed without asking.
    sent = next(c for c in p.calls if c['text']['format']['name'] == 'resume_keywords' and 'Other Inc' in c['input'])
    assert json.loads(sent['input'])['candidate_confirmed_keywords'] == ['reporting tools']


def test_failed_keyword_call_keeps_the_resume(setup):
    s, j, p, *_ = setup
    request = p.request
    def failing(endpoint, payload):
        if payload['text']['format']['name'] == 'resume_keywords':
            from app.retrieval import RetrievalError
            raise RetrievalError('OpenAI connection or response failed.', 502)
        return request(endpoint, payload)
    p.request = failing
    r = generate(s, j)
    assert r['state'] == 'DRAFT' and 'unavailable' in r['keyword_error']


def test_edit_needing_confirmation_stays_a_card_until_yes(setup):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT, 'question': 'Was the prototype a reporting tool?'}])
    r = generate(s, j)
    card = next(x for x in r['proposals'] if x['operation'] == 'keyword')
    assert not card.get('inline') and r['draft']['blocks'][-1]['text'] != KEYWORD_TEXT  # Not placed before confirmation.
    updated = s.decide_proposal(j, ProposalDecision(revision=r['revision'], proposal_id=card['id'], decision='accept'))['record']
    assert updated['draft']['blocks'][-1]['text'] == KEYWORD_TEXT
    assert updated['draft']['blocks'][-1]['reason'].endswith('You confirmed: Was the prototype a reporting tool?')


def test_declined_keyword_is_not_suggested_again_after_recheck(setup):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': KEYWORD_TEXT}])
    r = generate(s, j)
    card = next(x for x in r['proposals'] if x['operation'] == 'keyword')
    updated = s.decide_proposal(j, ProposalDecision(revision=r['revision'], proposal_id=card['id'], decision='reject'))['record']
    assert updated['draft']['blocks'][-1] == card['before'][0]
    s.recheck(j, Action(revision=updated['revision'], confirmed=True)); s.worker.join(5)
    again = s.view(j)['record']
    assert [x['status'] for x in again['proposals'] if x['operation'] == 'keyword'] == ['rejected']
    assert any('declined' in w for w in again['keyword_warnings'])


@pytest.mark.parametrize('text,problem', [
    # JobSuit-style rewrites that change the bullet rather than add a keyword.
    ('Applied product management practices to build reporting tools for 12 users during a pilot.', 'opening verb'),
    ('Built reporting tools during a pilot.', 'number'),
    ('Built a prototype of reporting tools for 12 users during a pilot, leveraging data.', 'filler'),
    ('Built a prototype of reporting tools for 12 users during a pilot across finance, sales, support and executive teams worldwide.', 'too long'),
])
def test_keyword_edit_that_changes_the_bullet_is_skipped(setup, text, problem):
    s, j, p, *_ = setup
    keyword_review(p, [{'text': text}])
    r = generate(s, j)
    assert not [x for x in r['proposals'] if x['operation'] == 'keyword']
    assert problem in ' '.join(r['keyword_warnings']).lower()
    row = next(k for k in r['keywords'] if k['keyword'] == 'reporting tools')
    # Supported but refused by a guard is reported separately from no evidence.
    assert row['status'] == 'unplaced' and problem in row['reason'].lower()


def test_skills_line_keeps_only_new_keywords():
    blocks = [{'kind': 'heading', 'item_id': 'h1', 'text': 'Skills', 'citations': []},
              {'kind': 'text', 'item_id': 's1', 'text': 'Technical: SQL, Python', 'citations': []}]
    coverage = KeywordCoverage.model_validate({'keywords': [], 'edits': [
        {'item_id': 's1', 'keywords': ['Python', 'Guardrails'], 'text': 'Technical: SQL, Python, Guardrails', 'question': '',
         'reason': 'Supported.', 'jd_quote': 'Build guardrails', 'reads_naturally': True}]})
    _, created, _ = keyword_proposals(coverage, blocks, 'Build guardrails with Python.', [])
    assert created[0]['keywords'] == ['Guardrails']


def test_keyword_check_keeps_named_tools_and_allows_skills_line_additions():
    old = {'text': 'Led a team of 3 to automate deployments with Jenkins, Docker, and Kubernetes, reducing release time by 9 hours.'}
    edit = lambda text, keywords: KeywordEdit(item_id='x', keywords=keywords, text=text, question='', reason='', jd_quote='', reads_naturally=True)
    dropped = edit('Led a team of 3 to automate releases and containerize workloads with Docker and Kubernetes, reducing release time by 9 hours.', ['containerization'])
    assert 'jenkins' in keyword_check(dropped, old, False)
    kept = edit('Led a team of 3 to automate deployments with Jenkins, Docker, and Kubernetes containerization, reducing release time by 9 hours.', ['containerization'])
    assert keyword_check(kept, old, False) == ''
    assert 'one new keyword' in keyword_check(edit(kept.text, ['containerization', 'release automation']), old, False)
    skills = {'text': 'Technical: GCP, Docker, Kubernetes'}
    assert keyword_check(edit('Technical: GCP, Docker, Kubernetes, Containerization, Release Automation', ['Containerization', 'Release Automation']), skills, True) == ''
    assert keyword_check(edit('Technical: Containerization, Docker', ['Containerization']), skills, True)


def test_keyword_edits_never_touch_summary_or_repeat_keywords():
    blocks = [{'kind': 'heading', 'item_id': 'h1', 'text': 'Summary', 'citations': []},
              {'kind': 'text', 'item_id': 's1', 'text': 'Product leader for data platforms.', 'citations': []},
              {'kind': 'heading', 'item_id': 'h2', 'text': 'Experience', 'citations': []},
              {'kind': 'bullet', 'item_id': 'b1', 'text': 'Shipped forecasting models for 3 plants.', 'citations': []},
              {'kind': 'bullet', 'item_id': 'b2', 'text': 'Ran SQL analysis for 40 accounts.', 'citations': []}]
    edit = lambda item, text, keyword: {'item_id': item, 'keywords': [keyword], 'text': text, 'question': '', 'reason': 'Supported.',
                                        'jd_quote': 'Own forecasting', 'reads_naturally': True}
    coverage = KeywordCoverage.model_validate({'keywords': [], 'edits': [
        edit('s1', 'Product leader for data platforms and time-series forecasting.', 'time-series forecasting'),
        edit('b1', 'Shipped time-series forecasting models for 3 plants.', 'time-series forecasting'),
        edit('b2', 'Ran SQL time-series forecasting analysis for 40 accounts.', 'time-series forecasting'),
        edit('b2', 'Ran SQL analysis for 40 accounts in Snowflake.', 'Snowflake'),
        edit('b1', 'Shipped forecasting models in Python for 3 plants.', 'Python')]})
    _, created, warnings = keyword_proposals(coverage, blocks, 'Own forecasting products.', [])
    # Summary refused, repeated keyword refused, second edit to b1 refused.
    assert [(c['target_ids'], c['keywords']) for c in created] == [(['b1'], ['time-series forecasting']), (['b2'], ['Snowflake'])]
    assert len(warnings) == 3


def test_draft_prepared_with_resume_approach_can_be_rechecked(setup):
    s, j, p, *_ = setup
    r = generate(s, j)
    key = s.engine.get('tailoring:latest:' + j.id)
    record = s.engine.get(key)
    record['context']['resume_approach'] = {'approach': 'TAILOR', 'state': 'COMPLETE'}
    s.engine.put(key, record)
    s.recheck(j, Action(revision=r['revision'], confirmed=True)); s.worker.join(5)
    assert s.view(j)['record']['state'] == 'DRAFT'


def test_literal_coverage_is_decided_by_code_with_whole_words():
    blocks = [{'kind': 'bullet', 'item_id': 'b1', 'text': 'Ran LLM evaluations on HTML reports.', 'citations': []}]
    row = lambda keyword: {'keyword': keyword, 'jd_quote': '', 'priority': 'required', 'importance': 'important', 'status': 'missing', 'reason': ''}
    coverage = KeywordCoverage.model_validate({'keywords': [row('LLMs'), row('TM'), row('evaluations')], 'edits': []})
    rows, _, _ = keyword_proposals(coverage, blocks, '', [])
    assert [r['status'] for r in rows] == ['present', 'missing', 'present']


def test_skills_line_takes_at_most_three_additions():
    old = {'text': 'Technical: SQL'}
    edit = KeywordEdit(item_id='x', keywords=['A1', 'B2', 'C3', 'D4'], text='Technical: SQL, A1, B2, C3, D4', question='', reason='', jd_quote='', reads_naturally=True)
    assert 'three' in keyword_check(edit, old, True)


def test_light_edit_cannot_drop_a_method_word():
    old = {'text': 'Shaped product strategy from 50+ user and stakeholder interviews, winning a pitch award.'}
    edit = KeywordEdit(item_id='x', keywords=['customer discovery'], question='', reason='', jd_quote='', reads_naturally=True,
                       text='Shaped product strategy from customer discovery with 50+ users and stakeholders, winning a pitch award.')
    assert 'dropped wording' in keyword_check(edit, old, False).lower()


def test_punctuation_after_a_name_is_not_a_dropped_name():
    old = {'text': 'Prioritized App of the Day from 13 ideas.'}
    edit = KeywordEdit(item_id='x', keywords=['roadmap'], question='', reason='', jd_quote='', reads_naturally=True,
                       text='Prioritized App of the Day on the roadmap from 13 ideas.')
    assert keyword_check(edit, old, False) == ''


def test_only_essential_keywords_may_redraft_a_bullet():
    old = {'text': 'Built a reporting prototype for 12 users during a pilot.'}
    redraft = KeywordEdit(item_id='x', keywords=['self-serve analytics'], question='', reason='', jd_quote='', reads_naturally=True,
                          text='Delivered self-serve analytics by building a reporting prototype for 12 users during a pilot.')
    assert 'opening verb' in keyword_check(redraft, old, False)
    assert keyword_check(redraft, old, False, essential=True) == ''


def test_importance_uses_title_repetition_requirements_and_nice_to_have():
    from app.tailoring import keyword_importance
    job = {'title': 'Voice AI Product Manager', 'description': 'Own voice AI. Build automation for calls with automation tools; automation everywhere. Nice: Rust.'}
    row = lambda keyword, importance, priority='required': {'keyword': keyword, 'importance': importance, 'priority': priority}
    requirements = [{'category': 'requirement', 'quote': 'Experience with SQL'}]
    assert keyword_importance(row('voice AI', 'minor'), job, requirements) == ('essential', ['In the job title'])
    assert keyword_importance(row('automation', 'minor'), job, requirements)[0] == 'important'
    assert keyword_importance(row('SQL', 'minor'), job, requirements) == ('important', ['In the requirements'])
    assert keyword_importance(row('Rust', 'essential', 'nice_to_have'), job, requirements)[0] == 'minor'


def test_minor_keyword_never_goes_into_a_bullet():
    blocks = [{'kind': 'bullet', 'item_id': 'b1', 'text': 'Ran SQL analysis for 40 accounts.', 'citations': []}]
    coverage = KeywordCoverage.model_validate({
        'keywords': [{'keyword': 'Snowflake', 'jd_quote': 'Snowflake', 'priority': 'nice_to_have', 'importance': 'minor', 'status': 'suggested', 'reason': ''}],
        'edits': [{'item_id': 'b1', 'keywords': ['Snowflake'], 'text': 'Ran SQL analysis in Snowflake for 40 accounts.', 'question': '',
                   'reason': 'Supported.', 'jd_quote': 'Snowflake', 'reads_naturally': True}]})
    rows, created, _ = keyword_proposals(coverage, blocks, 'Nice to have: Snowflake.', [])
    assert not created and rows[0]['status'] == 'unplaced'


def test_generic_single_words_are_not_keywords():
    blocks = [{'kind': 'bullet', 'item_id': 'b1', 'text': 'Built data systems with AI.', 'citations': []}]
    row = lambda keyword: {'keyword': keyword, 'jd_quote': '', 'priority': 'required', 'importance': 'important', 'status': 'present', 'reason': ''}
    coverage = KeywordCoverage.model_validate({'keywords': [row('AI'), row('data'), row('voice AI'), row('SQL')], 'edits': []})
    rows, _, _ = keyword_proposals(coverage, blocks, '', [])
    assert [r['keyword'] for r in rows] == ['voice AI', 'SQL']


def test_unchanged_bullets_are_not_checked_again(setup):
    s, j, p, *_ = setup
    r = generate(s, j)
    first = json.loads(reviews(p.calls)[0]['input'])
    assert first['blocks']  # The first preparation checks every statement.
    s.recheck(j, Action(revision=r['revision'], confirmed=True)); s.worker.join(5)
    again = json.loads(reviews(p.calls)[-1]['input'])
    assert again['blocks'] == [] and again['experience_bullets'] == []  # Nothing changed, nothing re-sent.
    record = s.view(j)['record']
    assert {f['block_index'] for f in record['checks']['findings']} >= {b['block_index'] for b in first['blocks']}
    assert record['state'] == 'DRAFT' and record['job_review']  # Saved results still pass; the JD review still runs.


def test_only_an_edited_bullet_is_checked_again(setup):
    s, j, p, *_ = setup
    r = generate(s, j)
    blocks = r['draft']['blocks']; blocks[-1]['text'] = 'During a pilot, built a reporting prototype for 12 users.'
    edited = s.edit(j, Edit(revision=r['revision'], blocks=blocks))['record']
    s.recheck(j, Action(revision=edited['revision'], confirmed=True)); s.worker.join(5)
    again = json.loads(reviews(p.calls)[-1]['input'])
    assert [b['block_index'] for b in again['blocks']] == [len(blocks) - 1]


def test_master_wording_is_used_as_written_and_never_corrected_from_stories(setup):
    s, j, p, *_ = setup
    request = p.request
    def correcting(endpoint, payload):
        response = request(endpoint, payload)
        if payload['text']['format']['name'] == 'resume_draft':
            data = json.loads(response['output'][0]['content'][0]['text'])
            master = json.loads(payload['input'])['master']
            data['edits'] = [{'item_id': master[-1]['item_id'], 'text': 'Built a reporting prototype for 12 pilot users.',
                              'citations': [{'source_id': 'e1', 'quote': 'At Example Labs, Alex built a reporting prototype for 12 users during a pilot.'}],
                              'reason': 'The story says the users were in a pilot.', 'change_type': 'fact_correction', 'jd_quote': ''}]
            response['output'][0]['content'][0]['text'] = json.dumps(data)
        return response
    p.request = correcting
    r = generate(s, j)
    master = r['context']['master']
    assert r['draft']['blocks'][-1]['text'] == master[-1]['text']  # The story did not rewrite the master.
    assert any(q.startswith('Possible conflict with your stories') for q in r['draft']['questions'])
    review = json.loads(reviews(p.calls)[0]['input'])
    assert review['blocks'] == []  # Nothing the model wrote, so nothing to audit.
    assert r['state'] == 'DRAFT'


def test_skills_line_additions_follow_the_line_capitalization_and_skip_the_prose_check():
    blocks = [{'kind': 'heading', 'item_id': 'h1', 'text': 'Skills', 'citations': []},
              {'kind': 'text', 'item_id': 's1', 'text': 'Technical: AI Agents, RAG, Data Products.', 'citations': []}]
    coverage = KeywordCoverage.model_validate({'keywords': [], 'edits': [
        {'item_id': 's1', 'keywords': ['large language models', 'model evaluation'], 'text': 'anything the model wrote', 'question': '',
         'reason': 'Supported.', 'jd_quote': 'Build LLM features', 'reads_naturally': False}]})
    _, created, _ = keyword_proposals(coverage, blocks, 'Build LLM features.', [])
    assert created[0]['text'] == 'Technical: AI Agents, RAG, Data Products, Large Language Models, Model Evaluation.'


def test_edit_without_a_question_must_quote_the_candidates_own_words():
    blocks = [{'kind': 'bullet', 'item_id': 'b1', 'text': 'Defined SARIMA scrap-price models for 3 plants.', 'citations': []},
              {'kind': 'bullet', 'item_id': 'b2', 'text': 'Prioritized App of the Day from 13 ideas.', 'citations': []}]
    edit = lambda item, text, keyword, quote: {'item_id': item, 'keywords': [keyword], 'text': text, 'question': '', 'reason': 'Implied.',
                                               'jd_quote': 'forecasting', 'evidence_quote': quote, 'reads_naturally': True}
    coverage = KeywordCoverage.model_validate({'keywords': [], 'edits': [
        edit('b1', 'Defined SARIMA scrap-price time-series forecasting models for 3 plants.', 'time-series forecasting', 'SARIMA'),
        edit('b2', 'Prioritized App of the Day from 13 ideas, including personalization.', 'personalization', 'personalized recommendations')]})
    _, created, _ = keyword_proposals(coverage, blocks, 'Own forecasting and personalization.', [], evidence='Defined SARIMA scrap-price models.')
    grounded, guessed = created
    assert grounded['question'] == ''  # Proven by the candidate's own word, so it can be placed.
    assert guessed['question'].startswith('Is this accurate')  # A guess always asks first.


def test_model_cannot_claim_a_keyword_is_present_when_the_resume_lacks_it():
    blocks = [{'kind': 'bullet', 'item_id': 'b1', 'text': 'Built RAG features with Docker for 3 teams.', 'citations': []}]
    row = lambda keyword: {'keyword': keyword, 'jd_quote': '', 'priority': 'required', 'importance': 'important', 'status': 'present', 'reason': 'Model says so.'}
    coverage = KeywordCoverage.model_validate({'keywords': [row('Kubernetes'), row('retrieval-augmented generation'), row('Docker')], 'edits': []})
    rows, _, _ = keyword_proposals(coverage, blocks, '', [])
    assert [r['status'] for r in rows] == ['unplaced', 'present', 'present']  # Kubernetes is absent; RAG covers its expansion.
    assert 'not on your resume' in rows[0]['reason']


def test_ready_list_shows_unapplied_prepared_resumes_best_fit_first(setup):
    from types import SimpleNamespace
    s, j, p, *_ = setup
    second = SimpleNamespace(id='job-2', title='Data PM', company='Other Inc', description=j.description, source_url='https://example.com/2')
    third = SimpleNamespace(id='job-3', title='AI PM', company='Third Co', description=j.description, source_url='')
    for job in (j, second, third): generate(s, job)
    jobs = {job.id: job for job in (j, second, third)}
    scores = {'job-1': {'assessment': {'score': 55, 'recommendation': 'Apply normally'}},
              'job-2': {'assessment': {'score': 81, 'recommendation': 'Attack hard'}}}
    rows = s.ready(jobs, scores, applied={'job-3'})
    assert [r['job_id'] for r in rows] == ['job-2', 'job-1']  # Applied jobs drop out; best fit first.
    assert rows[0]['url'] == 'https://example.com/2' and rows[0]['to_answer'] == 0
