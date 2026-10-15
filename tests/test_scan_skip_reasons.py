from app.discovery import Discovery


def test_skip_reasons_follow_eligibility_and_preserve_handled_decisions():
    base = {'keyword_match': {'priority': 'high'}, 'description': 'Own the product roadmap and partner with engineering to ship customer features. ' * 8, 'location_match': {'status': 'match'}, 'assessment': {'state': 'NOT_STARTED'}}
    assert Discovery.auto_eligible(base)
    assert Discovery.automatic_skip_reason(base) == ''
    for fields, expected in [
        ({'applied': True}, 'Already applied'),
        ({'passed': True}, 'Passed by you'),
        ({'assessment': {'state': 'COMPLETE'}}, 'Completed analysis'),
        ({'assessment': {'state': 'RUNNING'}}, 'already running'),
        ({'age_state': 'older_than_four_days'}, 'four-day'),
        ({'location_match': {'status': 'unclear'}}, 'location'),
        ({'company_fit': {'automatic_allowed': False}}, 'company preference'),
        ({'sponsorship': {'quote': 'No sponsorship'}}, 'sponsorship'),
        ({'description': ''}, 'job description'),
        ({'automatic_assessment': {'state': 'FAILED'}}, 'previous automatic attempt'),
    ]:
        item = {**base, **fields}
        assert not Discovery.auto_eligible(item)
        assert expected.lower() in Discovery.automatic_skip_reason(item).lower()
    kept = {**base, 'age_state': 'older_than_four_days', 'retention_protected': True}
    assert Discovery.auto_eligible(kept)
    assert Discovery.automatic_skip_reason(kept) == ''
