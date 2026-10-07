"""Isolate automatic public JD extraction so DNS or parsing cannot stall a scan."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .safe_fetch import ExtractionError

DESCRIPTION_SECONDS = 75


def extract_bounded(url, *, timeout=DESCRIPTION_SECONDS):
    # subprocess.run kills and waits for the child on timeout; no orphan fetch threads.
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'app.description_worker'],
            input=json.dumps({'url': url}), capture_output=True, text=True,
            encoding='utf-8', timeout=timeout,
            cwd=Path(__file__).resolve().parents[1],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except subprocess.TimeoutExpired:
        raise ExtractionError('Full JD fetching timed out. The scan continued; paste the description to assess this role.') from None
    if result.returncode:
        raise ExtractionError('Full JD extraction stopped unexpectedly. Paste the description to assess this role.')
    try:
        response = json.loads(result.stdout)
        if 'error' in response:
            raise ExtractionError(response['error'])
        return response['draft']
    except (ValueError, KeyError, TypeError):
        raise ExtractionError('Full JD extraction returned unreadable data. Paste the description.') from None


if __name__ == '__main__':
    from .extraction import extract_job
    try:
        draft = extract_job(json.load(sys.stdin)['url'])
        print(json.dumps({'draft': draft.model_dump(mode='json')}))
    except ExtractionError as exc:
        print(json.dumps({'error': str(exc)}))
