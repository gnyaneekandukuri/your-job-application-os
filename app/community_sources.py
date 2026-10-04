"""Public hiring sources. Source timestamps never imply an employer publication date."""
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

from .extraction import plain_text

HN = 'https://hacker-news.firebaseio.com/v0/'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def timestamp(value, milliseconds=False):
    try:
        return datetime.fromtimestamp(float(value) / (1000 if milliseconds else 1), timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def hn_listing(item, thread_id, current):
    if not isinstance(item, dict) or item.get('deleted') or item.get('dead'):
        return None
    if item.get('type') != 'comment' or item.get('parent') != thread_id:
        return None
    posted = timestamp(item.get('time'))
    if not posted or not current - timedelta(hours=24) <= datetime.fromisoformat(posted) <= current:
        return None
    text = plain_text(item.get('text', ''))
    # A hiring comment may advertise several roles. Require an explicit PM title,
    # not merely "work with product managers" somewhere in company prose.
    lines = text.splitlines()
    if not lines or '|' not in lines[0]:
        return None
    header = [part.strip() for part in lines[0].split('|')]
    candidates = header[1:] + [line.lstrip('-*• ') for line in lines[1:]]
    title = next((line for line in candidates if re.search(
        r'^(?:(?:senior|sr\.?|staff|principal|technical|ai|lead|group|founding|associate)\s+){0,4}'
        r'(?:product manager|head of product|director of product|product lead)\b', line, re.I)), None)
    if not title:
        return None
    return {'source_id': str(item['id']), 'title': title[:200], 'company': header[0][:200],
            'location': ' | '.join(header[1:])[:500],
            'url': f'https://news.ycombinator.com/item?id={item["id"]}',
            'description': text[:50000], 'posted_at': posted,
            'date_label': 'Hiring comment posted; employer vacancy date unknown',
            'date_basis': 'hiring_comment_timestamp', 'funding': 'Unknown',
            'snapshot_sha256': digest(item)}


def collect_hn(client, fetch, current=None):
    current = current or datetime.now(timezone.utc)
    account = json.loads(fetch(client, HN + 'user/whoishiring.json'))
    if not isinstance(account, dict) or not isinstance(account.get('submitted'), list):
        raise ValueError('HN account response changed')
    thread = None
    for ident in account['submitted'][:9]:
        candidate = json.loads(fetch(client, HN + f'item/{int(ident)}.json'))
        if (isinstance(candidate, dict) and candidate.get('by') == 'whoishiring'
                and candidate.get('type') == 'story'
                and candidate.get('title', '').startswith('Ask HN: Who is hiring?')):
            thread = candidate
            break
    if not thread or not isinstance(thread.get('kids', []), list):
        raise ValueError('Current HN hiring thread not found')
    warnings, rows = [], []
    ids = thread.get('kids', [])[:400]
    def read(ident):
        try:
            data = json.loads(fetch(client, HN + f'item/{int(ident)}.json'))
            if data is None:
                return None, 'One hiring comment was unavailable.'
            return hn_listing(data, thread['id'], current), None
        except Exception:
            return None, 'One hiring comment could not be read; coverage is partial.'
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row, warning in pool.map(read, ids):
            if row:
                rows.append(row)
            if warning and warning not in warnings:
                warnings.append(warning)
    if len(thread.get('kids', [])) > 400:
        warnings.append('Reached the 400-comment limit; coverage is partial.')
    return rows, ('Partial coverage: explicit PM titles in top-level hiring comments posted in the past 24 hours. '
                  'Comment dates are not vacancy creation dates. Informal or ambiguous ads may be missed.'), warnings


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.current = ''

    def handle_data(self, data):
        if self.current is not None:
            self.current += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append(self.current)
            self.current = None


def find_snapshot(value):
    if isinstance(value, dict):
        if isinstance(value.get('snapshot'), dict) and 'search' in value['snapshot']:
            return value['snapshot']
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        result = find_snapshot(child)
        if result is not None:
            return result


def cosign_rows(raw):
    from .discovery_sources import canonical, relevant
    parser = Scripts(); parser.feed(raw)
    snapshot = None
    for script in parser.scripts:
        if not script.startswith('self.__next_f.push('):
            continue
        try:
            block = json.loads(script[len('self.__next_f.push('):].rstrip(';')[:-1])
            if len(block) == 2 and isinstance(block[1], str):
                snapshot = find_snapshot(json.loads(block[1].split(':', 1)[1]))
                if snapshot is not None:
                    break
        except (ValueError, TypeError, IndexError):
            continue
    page = (snapshot or {}).get('search', {}).get('page', {}).get('page')
    if not isinstance(page, list):
        raise ValueError('Cosign public listing format changed')
    rows = []
    for job in page:
        if job.get('closed') or not relevant(job.get('title', '')):
            continue
        description = job.get('description') or ''
        # React Flight references are not job descriptions.
        if description.startswith('$'):
            description = ''
        rows.append({'source_id': str(job['postingId']), 'title': job['title'],
                     'company': job.get('organizationName') or 'Unknown',
                     'location': job.get('location') or '',
                     'url': canonical(job.get('url') or job.get('applyUrl')),
                     'description': plain_text(description)[:50000],
                     'posted_at': timestamp(job.get('postedAt'), milliseconds=True),
                     'date_label': 'Cosign posting timestamp', 'date_basis': 'board_timestamp',
                     'funding': 'Unknown', 'snapshot_sha256': digest(job)})
    return rows


def lenny_rows(data):
    from .discovery_sources import canonical, relevant
    results = data.get('results') if isinstance(data, dict) else None
    if not isinstance(results, list) or not results or not isinstance(results[0].get('hits'), list):
        raise ValueError('Lenny public search format changed')
    rows = []
    for job in results[0]['hits']:
        if not relevant(job.get('title', '')):
            continue
        rows.append({'source_id': str(job['job_id']), 'title': job['title'],
                     'company': job.get('company_name') or 'Unknown', 'location': job.get('location') or '',
                     'url': canonical(job['url']), 'description': '', 'posted_at': None,
                     'date_label': 'Posting date unknown; public results expose an update date only',
                     'date_basis': 'unknown', 'source_updated_at': job.get('updated_at'),
                     'funding': 'Unknown', 'snapshot_sha256': digest(job)})
    return rows


def collect_lenny(stop=None):
    # Let the public website make its own requests. No private API signatures,
    # account sessions, gated date filters or membership limits are reproduced.
    process = subprocess.Popen([sys.executable, '-m', 'app.lenny_public'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 75
    try:
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            try:
                output, _ = process.communicate(timeout=1)
                if process.returncode:
                    raise ValueError('Lenny public browser read failed; review source access')
                return lenny_rows(json.loads(output)), ('Partial coverage: first public PM result page only. '
                    'Date filtering requires account/subscription access; no 24-hour posting claim. Full descriptions are fetched separately.'), []
            except subprocess.TimeoutExpired:
                continue
        raise ValueError('Lenny public browser read stopped or timed out')
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
