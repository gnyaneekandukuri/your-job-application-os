import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from app.selected_assessments import SelectedQueue


def test_queued_roles_report_their_place_in_line(tmp_path):
    path = tmp_path / 'd.sqlite3'
    @contextmanager
    def db():
        conn = sqlite3.connect(path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    queue = SelectedQueue(SimpleNamespace(db=db))
    with db() as conn:
        conn.executemany('INSERT INTO selected_assessments VALUES(?,?,?,?)',
                         [('running', 'h', 'RUNNING', ''), ('first', 'h', 'QUEUED', ''),
                          ('done', 'h', 'COMPLETE', ''), ('second', 'h', 'QUEUED', '')])
    assert queue.positions() == {'first': 1, 'second': 2}
