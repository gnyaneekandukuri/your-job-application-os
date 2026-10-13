"""Durable, explicitly selected assessments; one job at a time, no failed-call retries."""
import json
from threading import Thread
from pydantic import BaseModel, ConfigDict, Field
from .store import Conflict
from .cost_tracking import opportunity_scope
from .needs_help import description_problem


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class SelectedAssessments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    roles: list[Selection] = Field(min_length=1, max_length=100)


class SelectedQueue:
    def __init__(self, discovery):
        self.d, self.worker = discovery, None
        with self.d.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS selected_assessments (
                opportunity_id TEXT PRIMARY KEY, content_hash TEXT, state TEXT, error TEXT)''')
            db.execute("UPDATE selected_assessments SET state='NEEDS_ATTENTION',error='Server stopped during analysis. Review before retrying.' WHERE state='RUNNING'")

    def enqueue(self, data):
        with self.d.lock:
            accepted, rejected = [], []
            for selected in {r.id:r for r in data.roles}.values():
                item = self.lookup(selected.id)
                reason = ('Role no longer exists.' if not item else
                          'Listing changed. Review it before selecting again.' if selected.content_hash != item['content_hash'] or item['description_changed'] else
                          'Add the full JD first.' if len(item.get('description','')) < 200 else
                          'Replace the job description first; it includes page text that cannot be analyzed.' if description_problem(item.get('description','')) else
                          'Already being assessed or complete.' if item.get('assessment',{}).get('state') in ('COMPLETE','RUNNING') or item.get('queued_for_assessment') else None)
                if reason:
                    rejected.append({'id':selected.id,'reason':reason}); continue
                with self.d.db() as db:
                    row=db.execute('SELECT state FROM selected_assessments WHERE opportunity_id=?',(selected.id,)).fetchone()
                    if not row or row[0] not in ('QUEUED','RUNNING'):
                        db.execute('INSERT OR REPLACE INTO selected_assessments VALUES(?,?,?,?)',(selected.id,selected.content_hash,'QUEUED',''))
                accepted.append(selected.id)
            self.start()
            return {'accepted':accepted,'rejected':rejected}

    def lookup(self, oid):
        """Only the fields enqueue checks, read for one listing; the full view takes ~30s."""
        with self.d.db() as db:
            row = db.execute('SELECT * FROM opportunities WHERE id=?', (oid,)).fetchone()
        if not row: return None
        item = json.loads(row['payload'])
        content_hash = self.d.content_hash(item)
        assessment = self.d.assessment_summary({'job_id': row['job_id'], 'description': item.get('description', '')})
        return {**item, 'id': oid, 'content_hash': content_hash, 'assessment': assessment,
                'description_changed': bool(row['imported_hash'] and row['imported_hash'] != content_hash),
                'queued_for_assessment': oid in self.d.queued_ids}

    def start(self):
        with self.d.lock:
            if self.worker and self.worker.is_alive(): return
            with self.d.db() as db:
                if not db.execute("SELECT 1 FROM selected_assessments WHERE state='QUEUED'").fetchone():return
            self.worker=Thread(target=self.run,daemon=True);self.worker.start()

    def finish(self, oid, state, error=''):
        with self.d.db() as db:
            db.execute('UPDATE selected_assessments SET state=?,error=? WHERE opportunity_id=?',(state,error,oid))

    def run(self):
        from .retrieval import RetrievalError
        while not self.d.stop.is_set():
            # Let the current discovery scan and automatic handoff finish first.
            if self.d.worker and self.d.worker.is_alive():
                self.d.stop.wait(.5); continue
            with self.d.lock, self.d.analysis.lock:
                if self.d.analysis.worker and self.d.analysis.worker.is_alive():
                    waiting=True
                else:
                    waiting=False
                    with self.d.db() as db:
                        selected=db.execute("SELECT * FROM selected_assessments WHERE state='QUEUED' ORDER BY rowid LIMIT 1").fetchone()
                        if not selected:return
                        row=db.execute('SELECT * FROM opportunities WHERE id=?',(selected['opportunity_id'],)).fetchone()
                    oid=selected['opportunity_id']
                    try:
                        if not row:raise Conflict('Role no longer exists.')
                        item=json.loads(row['payload'])
                        if self.d.content_hash(item)!=selected['content_hash']:raise Conflict('Listing changed while queued. Review and select again.')
                        self.finish(oid,'RUNNING')
                        with opportunity_scope(oid):
                            result=self.d.import_role(oid)
                        job_id=result['job_id']
                    except Exception as exc:
                        message=str(exc) if isinstance(exc,(Conflict,RetrievalError)) else 'Could not start analysis. Open the role to review.'
                        self.finish(oid,'NEEDS_ATTENTION',message)
                        if '429' in message:self.pause_remaining();return
                        continue
            if waiting:
                self.d.stop.wait(.5);continue
            while self.d.analysis.worker and self.d.analysis.worker.is_alive() and not self.d.stop.is_set():
                self.d.stop.wait(.5)
            if self.d.stop.is_set():return
            summary=self.d.assessment_summary({'job_id':job_id})
            message=summary.get('error','') or summary.get('recommendation_error','')
            self.finish(oid,'COMPLETE' if summary['state']=='COMPLETE' else 'NEEDS_ATTENTION',message or ('' if summary['state']=='COMPLETE' else 'Analysis needs review. Open Deep analysis.'))
            if '429' in message:self.pause_remaining();return

    def pause_remaining(self):
        with self.d.db() as db:
            db.execute("UPDATE selected_assessments SET state='NEEDS_ATTENTION',error='Provider limit reached. Select again after resolving billing or limits.' WHERE state='QUEUED'")
