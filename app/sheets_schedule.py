"""Durable change outbox with one sleeping timer, never a polling loop."""
import json
import sqlite3
import time
from threading import RLock, Timer, Event
from contextlib import closing


class SheetsSchedule:
    def __init__(self, sheets, clock=time.time, timer_factory=Timer):
        self.sheets=sheets
        self.path=sheets.applications.path
        self.clock, self.timer_factory=clock,timer_factory
        self.lock=RLock()
        self.timer=None
        self.closed=True
        self.active=False
        self.done=Event();self.done.set()

    def status(self):
        with closing(sqlite3.connect(self.path)) as db:
            first=db.execute('SELECT min(changed_at) FROM sheets_changes').fetchone()[0]
            error=db.execute('SELECT message FROM sheets_sync_error WHERE id=1').fetchone()
        return {'pending':first is not None,'due_at':first+3600 if first is not None else None,
                'error':error[0] if error else None,'syncing':self.active}

    def start(self):
        self.closed=False
        self.sheets.applications.on_change=self.arm
        # Upgrade existing workspaces once: don't send immediately or poll their history.
        data=self.sheets.read()
        last=data.get('last_sync') or {}
        rows=self.sheets.applications.list()
        if data.get('target') and (len(rows)!=last.get('total',0) or any(r.get('created_at','')>last.get('at','') for r in rows)):
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('INSERT INTO sheets_changes(changed_at) SELECT ? WHERE NOT EXISTS(SELECT 1 FROM sheets_changes)',(self.clock(),))
        self.arm()

    def arm(self):
        with self.lock:
            if self.closed or self.active:return
            state=self.status()
            if self.timer:
                self.timer.cancel();self.timer=None
            data=self.sheets.read()
            if not state['pending'] or state['error'] or not data.get('tokens') or not data.get('target'):return
            self.timer=self.timer_factory(max(0,state['due_at']-self.clock()),self.fire)
            self.timer.daemon=True
            self.timer.start()

    def fire(self):
        with self.lock:
            if self.closed or self.active:return
            self.timer=None
            state=self.status()
            if not state['pending'] or state['error']:return
            if state['due_at']>self.clock():self.arm();return
            self.active=True
            self.done.clear()
        try:
            self.sheets.sync(automatic=True)
        except Exception as exc:
            from .retrieval import RetrievalError
            message=str(exc) if isinstance(exc,RetrievalError) else 'Automatic sync failed. Your applications are saved. Use Sync to Google Sheets to retry.'
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('INSERT OR REPLACE INTO sheets_sync_error VALUES(1,?)',(message,))
        finally:
            with self.lock:self.active=False
            self.done.set()
            self.arm()

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN')
            seq=db.execute('SELECT coalesce(max(seq),0) FROM sheets_changes').fetchone()[0]
            rows=[self.sheets.applications.summary(json.loads(r[0])) for r in db.execute('SELECT payload FROM applications')]
        return seq,rows

    def acknowledge(self, seq):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM sheets_changes WHERE seq<=?',(seq,))
            db.execute('DELETE FROM sheets_sync_error')
        self.arm()

    def close(self):
        with self.lock:
            self.closed=True
            self.sheets.applications.on_change=None
            timer=self.timer
            if timer:timer.cancel()
        self.done.wait(60)
