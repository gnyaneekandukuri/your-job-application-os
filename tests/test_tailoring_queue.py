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


def test_nothing_starts_while_the_parallel_limit_is_reached():
    from app.tailoring import PARALLEL
    class Busy:
        def is_alive(self): return True
    t = make({'a': {'state': 'QUEUED', 'draft': None}}, ['a'])
    t.running = {f'busy{i}': Busy() for i in range(PARALLEL)}
    t.next_queued()
    assert t.engine['a']['state'] == 'QUEUED' and t.engine[QUEUE_KEY] == ['a']


def test_several_queued_resumes_are_prepared_at_once():
    from app.tailoring import PARALLEL
    started, release = [], Event()
    keys = [f'k{i}' for i in range(PARALLEL + 1)]
    t = make({k: {'state': 'QUEUED', 'draft': None} for k in keys}, list(keys))
    def run(key):
        started.append(key); release.wait(5); t.engine[key]['state'] = 'NEEDS_REVIEW'
    t.run = run
    t.next_queued()
    for _ in range(50):
        if len(started) == PARALLEL: break
        Event().wait(0.05)
    assert sorted(started) == sorted(keys[:PARALLEL])  # The first PARALLEL run together; the last one waits.
    assert t.engine[keys[-1]]['state'] == 'QUEUED'
    release.set()
    for _ in range(100):
        if t.engine[keys[-1]]['state'] == 'NEEDS_REVIEW': break
        Event().wait(0.05)
    assert t.engine[keys[-1]]['state'] == 'NEEDS_REVIEW'  # It starts as soon as a slot frees.
