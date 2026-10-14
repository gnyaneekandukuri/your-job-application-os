from threading import Event, RLock

from app.tailoring import QUEUE_KEY, Tailoring


class Engine(dict):
    def put(self, key, value): self[key] = value


def make(records, queue):
    t = Tailoring.__new__(Tailoring)
    t.engine, t.lock, t.worker, t.active = Engine(records), RLock(), None, None
    t.engine.put(QUEUE_KEY, queue)
    return t


def test_queued_requests_run_in_order_after_the_current_one():
    ran, done = [], Event()
    t = make({'a': {'state': 'QUEUED', 'draft': None}, 'b': {'state': 'QUEUED', 'draft': None},
              'gone': {'state': 'APPROVED', 'draft': None}}, ['gone', 'a', 'b'])
    def run(key):
        ran.append(key); t.engine[key]['state'] = 'NEEDS_REVIEW'
        if key == 'b': done.set()
    t.run = run
    t.next_queued()
    assert done.wait(5)
    assert ran == ['a', 'b'] and t.engine[QUEUE_KEY] == []


def test_nothing_starts_while_a_resume_is_being_prepared():
    class Busy:
        def is_alive(self): return True
    t = make({'a': {'state': 'QUEUED', 'draft': None}}, ['a'])
    t.worker = Busy()
    t.next_queued()
    assert t.engine['a']['state'] == 'QUEUED' and t.engine[QUEUE_KEY] == ['a']
