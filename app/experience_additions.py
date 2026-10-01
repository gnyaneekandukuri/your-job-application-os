"""User-authored additions: durable save first, separately evaluated evidence second."""
import json
from threading import Thread
from uuid import UUID
from pydantic import Field
from .job_requirements import Strict
from .retrieval import RetrievalError, digest, now
from .story_library import EditStory, PrepareStory


class ExperienceAddition(Strict):
    request_id: UUID
    source_id: str = Field(min_length=1, max_length=100)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=20, max_length=12000)
    consent_to_send: bool = False


def latest(manager, job):
    key = manager.engine.get('experience-latest:' + job.id)
    record = manager.engine.get(key) if key else None
    if record and record['state'] == 'UPDATING' and not (manager.worker and manager.worker.is_alive()):
        record = {**record, 'state': 'PENDING', 'message': 'Experience saved; update interrupted. Retry when ready.'}
    return record


def save_addition(manager, job, requirement_id, data):
    from .role_analysis import AnalysisRun, VERSION, MODEL
    library, engine = manager.library, manager.engine
    key = 'experience-addition:' + str(data.request_id)
    signature = digest(json.dumps({'job': job.id, 'requirement': requirement_id,
        **data.model_dump(mode='json', exclude={'consent_to_send'})}, sort_keys=True))
    with manager.lock, library.lock:
        existing = engine.get(key)
        if existing and existing['signature'] != signature:
            raise RetrievalError('This save request belongs to different content.', 409)
        if manager.worker and manager.worker.is_alive():
            if existing:
                return existing
            raise RetrievalError('Wait for the current analysis to finish before adding experience.', 409)
        if not existing:
            preview = manager.view(job)
            if preview['input_version'] != data.input_version or not preview['run']:
                raise RetrievalError('Report changed. Reload before adding experience.', 409)
            match = preview['run']['results'].get(requirement_id)
            if not match or match['judgment']['assessment']['alignment'] not in ('partial', 'insufficient_evidence'):
                raise RetrievalError('Choose a partial-alignment or insufficient-evidence requirement.', 409)
            source = library.detail(data.source_id)
            if source['category'] != 'story':
                raise RetrievalError('Choose an employer/project story, not coaching.', 409)
            if source['revision'] != data.expected_revision:
                raise RetrievalError('Story changed. Reload its preview before saving.', 409)
            # Persist intent before file mutation so a retry can recover after a crash.
            existing = {'signature': signature, 'job_id': job.id, 'requirement_id': requirement_id,
                'source_id': data.source_id, 'source_title': source['title'], 'source_path': source['path'],
                'state': 'SAVING', 'message': '', 'saved_at': now(),
                'request': data.model_dump(mode='json')}
            engine.put(key, existing)
        if existing['state'] == 'SAVING':
            source = library.detail(data.source_id)
            marker = '<!-- experience-addition:' + str(data.request_id) + ' -->'
            if marker not in source['text']:
                if source['revision'] != data.expected_revision:
                    raise RetrievalError('Story changed before save completed. Reload before trying again.', 409)
                text = (source['text'].rstrip() + '\n\n## User-reported experience addition — ' + existing['saved_at']
                    + '\n' + marker + '\nUser-reported context, not an approved resume claim.\n'
                    + 'Employer/project: ' + source['employer'] + ' / ' + source['project'] + '\n\n'
                    + data.text.strip() + '\n')
                library.edit(data.source_id, EditStory(title=source['title'], employer=source['employer'],
                    project=source['project'], text=text, expected_revision=source['revision']))
            existing.update(state='PENDING', message='Experience saved — analysis update pending. Previous score is outdated.')
            engine.put(key, existing)
            engine.put('experience-latest:' + job.id, key)
        if not data.consent_to_send or existing['state'] == 'COMPLETE':
            return existing
        existing.update(state='UPDATING', message='Experience saved. Preparing changed passages and rechecking this role…')
        engine.put(key, existing)

        def update():
            try:
                source = library.detail(data.source_id)
                library.prepare(data.source_id, PrepareStory(expected_revision=source['revision'], consent_to_send=True))
                version, versions, _, stories = manager.environment(job)
                if not stories or not all(s['ready'] for s in stories):
                    raise RetrievalError('Prepare the other changed stories before reassessment.', 409)
                run_key = manager.cache_key(job, version)
                saved = engine.get(run_key)
                run = AnalysisRun.model_validate(saved) if saved else AnalysisRun(job_id=job.id,
                    input_version=version, version=VERSION, model=MODEL, source_versions=versions,
                    state='RUNNING', stage='Reassessing saved experience…', created_at=now(), updated_at=now())
                manager.active_key = run_key
                engine.put('role-analysis-latest:' + job.id, run_key)
                manager.execute(job, run_key, run)
                existing.update(state='COMPLETE' if run.state == 'COMPLETE' else 'PENDING',
                    message='Experience saved and reassessed. Review the updated reasoning.' if run.state == 'COMPLETE'
                    else 'Experience saved — analysis update pending. ' + run.error)
            except Exception as exc:
                existing.update(state='PENDING', message='Experience saved — analysis update pending. ' +
                    (str(exc) if isinstance(exc, RetrievalError) else 'Processing failed; retry when ready.'))
            finally:
                engine.put(key, existing)
        manager.worker = Thread(target=update, daemon=True)
        manager.worker.start()
        return existing


def retry(manager, job):
    record = latest(manager, job)
    if not record:
        raise RetrievalError('No saved experience update to retry.', 404)
    return save_addition(manager, job, record['requirement_id'],
        ExperienceAddition(**{**record['request'], 'consent_to_send': True}))
