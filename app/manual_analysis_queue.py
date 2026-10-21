"""Durable user requests; wait for the shared worker, never retry paid failures."""
import json
from contextlib import closing
from threading import Event, RLock, Thread
from .role_analysis import AnalyzeInput
from .retrieval import RetrievalError, now

PREFIX = 'manual-analysis-queue:'


class ManualAnalysisQueue:
    def __init__(self, analysis, job_loader):
        self.analysis, self.engine, self.job_loader = analysis, analysis.engine, job_loader
        self.lock, self.stop, self.worker = RLock(), Event(), None
        self.draining = lambda: (self.engine.path.parent / 'maintenance.flag').exists()
        for row in self.entries():
            if row['state'] == 'RUNNING':
                run=self.engine.get('role-analysis:'+row['job_id']+':'+row['input_version'])
                if run and run['state']=='COMPLETE': self.save(row,'COMPLETE')
                else: self.save(row, 'NEEDS_ATTENTION', 'Server stopped during analysis. Review and retry; completed work is saved.')

    def entries(self):
        with closing(self.engine.connect()) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM cache WHERE key LIKE ?', (PREFIX+'%',))]

    def display_items(self):
        items=[]
        for row in self.entries():
            if row['state']=='COMPLETE':continue
            run=self.engine.get('role-analysis:'+row['job_id']+':'+row['input_version']) or {}
            state=row['state']
            if state=='RUNNING' and run.get('state')=='COMPLETE':continue
            items.append({**{k:row.get(k,'') for k in ('job_id','company','title','url')},
                'assessment':{'state':state,'score':None,'recommendation':None,
                    'stage':run.get('stage','') if state=='RUNNING' else 'Queued for deep analysis',
                    'error':row.get('error','')}})
        return items

    def pending(self):
        return any(r['state']=='QUEUED' for r in self.entries())

    def save(self, row, state, error=''):
        row={**row,'state':state,'error':error,'updated_at':now()}
        self.engine.put(PREFIX+row['job_id'],row)
        return row

    def enqueue(self, job, data):
        with self.lock:
            preview=self.analysis.view(job)
            if preview['input_version']!=data.input_version:
                raise RetrievalError('The job or context changed. Reload before queuing analysis.',409)
            if not preview['ready']:
                raise RetrievalError('Prepare the current stories before queuing analysis.',409)
            existing=self.engine.get(PREFIX+job.id)
            if existing and existing['state']=='QUEUED' and existing['input_version']!=data.input_version:
                # Stories or facts changed while waiting: keep its place, analyze the current context.
                row=self.save({**existing,'input_version':data.input_version},'QUEUED')
                self.start()
                return {**preview,'queue':row}
            if existing and existing['state'] in ('QUEUED','RUNNING'):
                return {**preview,'queue':existing}
            if preview.get('run') and preview['run']['state']=='COMPLETE':return preview
            row={'company':job.company,'title':job.title,'url':str(job.source_url or ''),'job_id':job.id,'input_version':data.input_version,'created_at':now()}
            row=self.save(row,'QUEUED')
            self.start()
            return {**preview,'queue':row}

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():return
            if self.stop.is_set():return
            self.worker=Thread(target=self.run,daemon=True,name='manual-analysis-queue')
            self.worker.start()

    def step(self):
        with self.analysis.lock:
            if self.stop.is_set() or self.draining():return
            if self.analysis.worker and self.analysis.worker.is_alive():return
            queued=sorted((r for r in self.entries() if r['state']=='QUEUED'),key=lambda r:r['created_at'])
            if not queued:return
            row=queued[0]
            try:
                job=self.job_loader(row['job_id'])
                preview=self.analysis.view(job)
                if preview['input_version']!=row['input_version']:
                    raise RetrievalError('The JD or context changed while queued. Review and request analysis again.',409)
                self.save(row,'RUNNING')
                result=self.analysis.start(job,AnalyzeInput(consent_to_send=True,input_version=row['input_version']))
                if (result.get('run') or {}).get('state')=='COMPLETE':self.save(row,'COMPLETE')
            except Exception as exc:
                self.save(row,'NEEDS_ATTENTION',str(exc) if isinstance(exc,RetrievalError) else 'Could not start analysis. Open the role to retry.')

    def reconcile(self):
        for row in self.entries():
            if row['state']!='RUNNING':continue
            run=self.engine.get('role-analysis:'+row['job_id']+':'+row['input_version'])
            if run and run['state'] not in ('RUNNING',):
                self.save(row,'COMPLETE' if run['state']=='COMPLETE' else 'NEEDS_ATTENTION',run.get('error',''))

    def run(self):
        while not self.stop.is_set():
            try:
                self.reconcile()
                self.step()
            except Exception:
                # A transient local read failure must not lose durable requests.
                pass
            self.stop.wait(.5)

    def close(self):
        self.stop.set()
        if self.worker:self.worker.join(2)
