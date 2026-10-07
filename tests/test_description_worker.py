import subprocess
import sys
import pytest
from app.description_worker import extract_bounded
from app.safe_fetch import ExtractionError


def test_timeout_kills_real_child(monkeypatch):
    original = subprocess.run
    def sleeping_child(command, **kwargs):
        return original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
    monkeypatch.setattr('app.description_worker.subprocess.run', sleeping_child)
    with pytest.raises(ExtractionError, match='timed out'):
        extract_bounded('https://example.com/job', timeout=0.2)


def test_real_worker_preserves_url_safety():
    with pytest.raises(ExtractionError):
        extract_bounded('http://127.0.0.1/private')


def test_worker_success_and_failure(monkeypatch):
    monkeypatch.setattr('app.description_worker.subprocess.run', lambda *a, **k:
        subprocess.CompletedProcess(a, 0, '{"draft":{"title":"PM"}}', ''))
    assert extract_bounded('https://example.com/job') == {'title':'PM'}
    monkeypatch.setattr('app.description_worker.subprocess.run', lambda *a, **k:
        subprocess.CompletedProcess(a, 1, '', 'private diagnostic'))
    with pytest.raises(ExtractionError, match='stopped unexpectedly'):
        extract_bounded('https://example.com/job')
