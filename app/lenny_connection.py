"""Local, isolated browser session for Lenny's Jobs.

The browser runs in a child process so Playwright's event loop never crosses
FastAPI/scheduler threads. Its profile and output stay in the private directory.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread

from .store import Conflict

DIRECTORY_URL = 'https://www.lennysjobs.com/'


class LennyConnectionError(Exception):
    def __init__(self, state, message):
        super().__init__(message)
        self.source_state = state
        self.discovery_diagnostic = {'reason':message, 'kind':state, 'http_status':None,
                                     'host':'www.lennysjobs.com','path':'/',
                                     'attempts':1,'causes':[]}


def save_status(root, state, message):
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'status.json'
    temp = root / 'status.tmp'
    temp.write_text(json.dumps({'state':state,'message':message,
                               'checked_at':datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
    # Windows may briefly hold the destination open during a status poll.
    for attempt in range(10):
        try:
            os.replace(temp, target)
            break
        except PermissionError:
            if attempt == 9: raise
            time.sleep(0.05)


class LennyConnection:
    def __init__(self, private_dir):
        self.root = Path(private_dir) / 'lenny-browser'
        self.lock = Lock()
        self.process = None
        self.thread = None

    def status(self):
        try:
            value = json.loads((self.root / 'status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            value = {'state':'NOT_CONNECTED','message':'Connect your Lenny account to collect Lenny jobs.'}
        busy = self.lock.locked()
        if not busy and value['state'] in ('CONNECTING','WAITING_FOR_LOGIN','SCANNING'):
            value = {**value,'state':'NEEDS_CHECK','message':'The previous browser operation stopped. Check the connection or sign in again.'}
        return {**value, 'busy':busy}

    def _run(self, command):
        self.root.mkdir(parents=True, exist_ok=True)
        output = self.root / 'result.json'
        output.unlink(missing_ok=True)
        env = {k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY', 'TYPESAFE_API_KEY')}
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'app.lenny_browser_worker', command, str(self.root.resolve())],
            cwd=Path(__file__).resolve().parents[1], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.process.wait(timeout=1000)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
            save_status(self.root,'ERROR','Lenny browser operation timed out. Try connecting again.')
            raise LennyConnectionError('error','Lenny browser operation timed out.')
        finally:
            self.process = None
        if not output.exists():
            save_status(self.root,'ERROR','Lenny browser could not start. Check the browser installation.')
            raise LennyConnectionError('error','Lenny browser could not start. Check the browser installation.')
        result = json.loads(output.read_text(encoding='utf-8'))
        if result.get('error'):
            raise LennyConnectionError(result.get('source_state','error'), result['error'])
        return result

    def start(self, command='login'):
        if not self.lock.acquire(blocking=False):
            raise Conflict('Lenny browser is busy. Wait for the current operation to finish.')
        save_status(self.root,'CONNECTING','Opening Lenny sign-in...' if command=='login' else 'Checking saved Lenny session...')
        def work():
            try:
                self._run(command)
            except LennyConnectionError:
                pass  # Worker already persisted a user-facing status.
            except Exception:
                save_status(self.root,'ERROR','Lenny browser failed to start. Check the browser installation.')
            finally:
                self.lock.release()
        self.thread = Thread(target=work, daemon=True)
        self.thread.start()
        return self.status()

    def collect(self, countries):
        if not (self.root / 'profile').exists():
            raise LennyConnectionError('login_required','Connect Lenny in Discovery runs before scanning this source.')
        if not self.lock.acquire(blocking=False):
            raise LennyConnectionError('busy','Lenny sign-in or connection check is in progress. Try the scan after it finishes.')
        try:
            (self.root / 'scan-options.json').write_text(json.dumps({'countries': countries}), encoding='utf-8')
            result = self._run('scan')
            return result['rows'], result['coverage'], result['warnings']
        finally:
            self.lock.release()

    def close(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
        if self.thread:
            self.thread.join(timeout=3)
