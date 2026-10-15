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


def test_scan_record_preserves_boards_and_new_flag_across_repeat_sightings(tmp_path):
    import json
    import sqlite3
    from contextlib import contextmanager
    from app.cost_tracking import scan_id
    path = tmp_path / 'scan.sqlite3'
    @contextmanager
    def db():
        with sqlite3.connect(path) as conn:
            yield conn
    with db() as conn:
        conn.execute('CREATE TABLE scan_roles (scan_id INTEGER, opportunity_id TEXT, payload TEXT, PRIMARY KEY(scan_id,opportunity_id))')
    discovery = object.__new__(Discovery)
    discovery.db = db
    token = scan_id.set(1)
    try:
        item = {'id': 'one', 'company': 'Example', 'title': 'PM'}
        discovery.record_scan_role(item, newly_saved=True, source_board='yc')
        discovery.record_scan_role(item, newly_saved=False, source_board='linkedin')
        discovery.record_scan_role(item, assessment={'state': 'COMPLETE'})
        with db() as conn:
            value = json.loads(conn.execute('SELECT payload FROM scan_roles').fetchone()[0])
        assert value['newly_saved'] is True
        assert value['source_boards'] == ['linkedin', 'yc']
        assert value['assessment']['state'] == 'COMPLETE'
        scan_id.set(2)
        discovery.record_scan_role(item, newly_saved=False, source_board='yc')
        with db() as conn:
            value = json.loads(conn.execute('SELECT payload FROM scan_roles WHERE scan_id=2').fetchone()[0])
        assert value['newly_saved'] is False
        assert value['source_boards'] == ['yc']
    finally:
        scan_id.reset(token)
