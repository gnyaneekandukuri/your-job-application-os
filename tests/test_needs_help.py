from app.needs_help import description_problem, needs_help

GOOD = 'Own the analytics roadmap and partner with engineering on governed metrics. ' * 5
NOISE = 'Country' + 'AlbaniaAlgeriaAndorra' * 80


def kinds(**item):
    return [h['kind'] for h in needs_help({'description': GOOD, **item})]


def test_description_problems_are_detected_without_model_calls():
    assert description_problem('') == 'missing'
    assert description_problem(GOOD) is None
    assert description_problem(GOOD + NOISE) == 'unreadable'


def test_one_plain_request_per_stuck_listing():
    assert kinds() == []
    assert kinds(description=GOOD + NOISE, assessment={'state': 'PAUSED', 'error': 'too long'}) == ['replace_jd']
    assert kinds(description='', description_fetch={'state': 'NEEDS_PASTE'}) == ['paste_jd']
    assert kinds(description='', description_fetch=None) == []  # the next scan still tries to fetch it
    assert kinds(description_changed=True) == ['review_jd']
    retry = needs_help({'description': GOOD, 'assessment': {'state': 'ERROR', 'error': 'Provider limit'}})
    assert retry[0]['kind'] == 'retry' and 'Provider limit' in retry[0]['message']
    assert kinds(assessment={'state': 'NEEDS_ATTENTION', 'error': 'Too many JD criteria'}) == ['review_analysis']
    assert kinds(assessment={'state': 'COMPLETE'}) == [] and kinds(completed=True, description='') == []
