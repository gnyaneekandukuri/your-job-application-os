from contextlib import closing
from threading import RLock
from types import SimpleNamespace

import pytest

from app.pursuit import Choose, PursuitService
from app.retrieval import digest
from test_jev_assessment import engine_at


@pytest.fixture
def saved_decision(tmp_path, monkeypatch):
    import app.pursuit as pursuit
    engine = engine_at(tmp_path)
    job = SimpleNamespace(id='saved-job')
    manager = SimpleNamespace(engine=engine, lock=RLock(), library=SimpleNamespace(lock=RLock()))
    manager.view = lambda job, **kwargs: {'run': {'state':'COMPLETE'}, 'score':None}
    service = PursuitService(manager)
    monkeypatch.setattr(service, 'inputs', lambda job: (digest(pursuit.INTERPRETATION_LOGIC), {'requirements':[]}, {}, {}, {}))
    event = {'job_id':job.id, 'choice':'CASUAL', 'reason':'Worth applying',
             'version':service.inputs(job)[0], 'created_at':'2026-10-19T12:00:00+00:00'}
    engine.put('pursuit-decision-event:old', event)
    monkeypatch.setattr(pursuit, 'INTERPRETATION_LOGIC', 'next-interpretation')
    return service, job, event


def test_decision_survives_version_change_and_new_choice_wins(saved_decision, monkeypatch):
    from langgraph.checkpoint.sqlite import SqliteSaver
    service, job, event = saved_decision
    view = service.view(job)
    assert view['result'] is None
    assert view['choice'] == {'choice':'CASUAL', 'reason':event['reason'],
                              'decided_at':event['created_at'], 'version':event['version'], 'carried_forward':True}
    assert service.view(job)['choice'] == view['choice']
    # Use the real review node and choose path, with no interpretation/provider call.
    inputs = service.inputs(job)
    with SqliteSaver.from_conn_string(service.path) as saver:
        graph = service.graph(saver, job, inputs)
        graph.update_state(service.config(job, inputs[0]),
                           {'result':{'recommendation':'CASUAL'}, 'status':'AWAITING_PURSUIT_DECISION'},
                           as_node='recommend_pursuit')
    monkeypatch.setattr('app.pursuit_explanation.explain', lambda *args: {'summary':'Current recommendation'})
    updated = service.choose(job, Choose(version=inputs[0], choice='ATTACK_HARD', reason='Prioritize'))
    assert updated['choice']['choice'] == 'ATTACK_HARD'
    assert not updated['choice'].get('carried_forward')
    assert updated['choice']['version'] == inputs[0]
    with closing(service.manager.engine.connect()) as db:
        assert db.execute("SELECT count(*) FROM cache WHERE key LIKE 'pursuit-decision-event:%'").fetchone()[0] == 2


def test_jobs_list_summary_uses_carried_choice(saved_decision, monkeypatch):
    from app.discovery import Discovery
    service, job, _ = saved_decision
    monkeypatch.setattr('app.pursuit.PursuitService', lambda manager: service)
    discovery = object.__new__(Discovery)
    discovery.analysis = service.manager
    discovery.saved_job = lambda job_id: job
    summary = discovery.saved_assessment(job.id)
    assert summary['choice'] == 'CASUAL'


def test_feedback_accepts_stable_carried_choice(saved_decision, monkeypatch):
    from uuid import uuid4
    from app import feedback_learning as feedback
    service, job, _ = saved_decision
    choice = service.view(job)['choice']
    monkeypatch.setattr(service, 'view', lambda job: {'choice':dict(choice), 'result':{'recommendation':'CASUAL', 'score':60}})
    monkeypatch.setattr(feedback, 'read_configs', lambda manager: {})
    monkeypatch.setattr(feedback, 'FILES', {})
    monkeypatch.setattr('app.jev_feedback.propose', lambda *args: feedback.Proposal(
        scope='role_only', diagnosis='Role exception', explanation='Keep this decision',
        question='', expected_impact='No global changes', changes=[]))
    record = feedback.analyze(service, job, feedback.AnalyzeFeedback(
        request_id=uuid4(), version=service.inputs(job)[0], consent_to_send=True))
    assert record['state'] == 'PROPOSED'
    assert record['choice'] == choice
