import json
import sqlite3
import time
from datetime import date
import httpx
from app.applications import Applications, ExternalApplication, UpdateApplication
from app.google_sheets import GoogleSheets, TAB, HEADERS
from app.sheets_schedule import SheetsSchedule

class Timer:
    def __init__(self,delay,callback):self.delay=delay;self.callback=callback;self.cancelled=False
    def start(self):pass
    def cancel(self):self.cancelled=True


def setup(tmp_path, handler=None):
    apps=Applications(tmp_path/'applications.sqlite3')
    calls=[]
    def response(req):
        calls.append(req)
        if handler:handler(req,apps)
        if req.method=='GET' and '/values/' not in req.url.path:
            return httpx.Response(200,json={'sheets':[{'properties':{'title':TAB,'sheetId':1}}]})
        return httpx.Response(200,json={'values':[HEADERS]} if req.method=='GET' else {})
    s=GoogleSheets(tmp_path,apps,httpx.MockTransport(response))
    s.write({'tokens':{'access_token':'test','expires_at':time.time()+9999},'target':{'id':'test'}})
    clock=[time.time()]
    q=SheetsSchedule(s,clock=lambda:clock[0],timer_factory=Timer);s.schedule=q;q.start()
    return apps,s,q,clock,calls


def add(apps,name='Example'):
    return apps.add_external(ExternalApplication(company=name,title='PM',applied_date=date(2026,10,22)))


def test_first_change_sets_deadline_later_changes_do_not_delay_and_idle_is_quiet(tmp_path):
    a,s,q,clock,calls=setup(tmp_path)
    assert q.timer is None and not calls
    row=add(a);due=q.status()['due_at'];assert 3599<q.timer.delay<=3600
    clock[0]+=500
    add(a,'Second')
    assert q.status()['due_at']==due and not calls
    a.update(row['id'],UpdateApplication(revision=row['revision'],status='Interviewing',notes='Updated'))
    assert q.status()['due_at']==due
    clock[0]=due-1;q.fire();assert not calls
    clock[0]=due;q.fire()
    assert calls and not q.status()['pending'] and q.timer is None
    n=len(calls);q.fire();assert len(calls)==n
    q.close()


def test_manual_success_cancels_timer_but_changes_during_sync_survive(tmp_path):
    a,s,q,clock,calls=setup(tmp_path)
    add(a);timer=q.timer
    s.sync()
    assert timer.cancelled and not q.status()['pending'] and q.timer is None
    n=len(calls);timer.callback();assert len(calls)==n
    add(a,'Next')
    changed=[]
    old=s.request
    def request(method,path,**kw):
        if '/values:batchUpdate' in path and not changed:
            changed.append(True);add(a,'During sync')
        return old(method,path,**kw)
    s.request=request;s.sync()
    assert q.status()['pending'] and q.timer is not None
    s.sync();assert not q.status()['pending']
    q.close()


def test_restart_keeps_original_deadline_and_overdue_timer_fires_once(tmp_path):
    a,s,q,clock,calls=setup(tmp_path)
    add(a);due=q.status()['due_at'];q.close()
    restarted=SheetsSchedule(s,clock=lambda:due+10,timer_factory=Timer);s.schedule=restarted
    restarted.start()
    assert restarted.status()['due_at']==due and restarted.timer.delay==0
    restarted.fire()
    assert calls and not restarted.status()['pending']
    restarted.close()


def test_failure_preserves_batch_and_error_without_retry_loop(tmp_path):
    a,s,q,clock,calls=setup(tmp_path)
    add(a);due=q.status()['due_at'];clock[0]=due
    from app.retrieval import RetrievalError
    original=s.request
    s.request=lambda *a,**kw:(_ for _ in ()).throw(RetrievalError('Google unavailable',502))
    q.fire()
    assert q.status()['pending'] and q.status()['error']=='Google unavailable' and q.timer is None
    add(a,'Later');assert q.timer is None and q.status()['due_at']==due
    q.close();q=SheetsSchedule(s,clock=lambda:due+60,timer_factory=Timer);s.schedule=q;q.start()
    assert q.timer is None
    s.request=original;s.sync()
    assert not q.status()['pending'] and not q.status()['error']
    q.close()


def test_duplicate_confirmation_and_unchanged_edit_do_not_schedule(tmp_path):
    a,s,q,clock,calls=setup(tmp_path)
    row=add(a);s.sync()
    add(a)
    a.update(row['id'],UpdateApplication(revision=row['revision'],status='Applied',notes=''))
    assert q.timer is None and not q.status()['pending']
    q.close()


def test_disconnected_keeps_outbox_without_network_requests(tmp_path):
    a,s,q,clock,calls=setup(tmp_path)
    add(a);s.disconnect()
    assert q.timer is None and q.status()['pending'] and not calls
    q.close()


def test_failed_manual_sync_does_not_cancel_pending_batch(tmp_path):
    import pytest
    from app.retrieval import RetrievalError
    a,s,q,clock,calls=setup(tmp_path)
    add(a);due=q.status()['due_at'];timer=q.timer
    s.request=lambda *a,**kw:(_ for _ in ()).throw(RetrievalError('Unavailable',502))
    with pytest.raises(RetrievalError):s.sync()
    assert q.status()['due_at']==due and q.status()['pending'] and not timer.cancelled
    q.close()
