"""Audit only: no constructors, workers, providers, or database writes.

Run: python scripts/audit_applied_matching.py
"""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.job_identity import AppliedMatchIndex, company_title_key, ats_keys
from app.job_list import JobList


def read(path, query):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute(query).fetchall()


def audit(root):
    records = [dict(json.loads(payload), job_id=jid) for jid, payload in
               read(root / 'applications.sqlite3', 'SELECT job_id,payload FROM applications')]
    cards = [dict(json.loads(payload), id=oid, job_id=jid) for oid, jid, payload in
             read(root / 'discovery.sqlite3', 'SELECT id,job_id,payload FROM opportunities')]
    reader = object.__new__(JobList)
    reader.path = root / 'job-list.sqlite3'
    reader.discovery = SimpleNamespace(path=root / 'discovery.sqlite3')
    ids, urls = reader.compute_applied_keys()
    counts = Counter()
    results = []
    for record in records:
        if not record['job_id'].startswith('external:'): continue
        index = AppliedMatchIndex([record])
        app = {**record.get('job_snapshot', {}), **record}
        company = company_title_key(app)[0]
        related = [c for c in cards if index.reason(c)]
        plausible = [c for c in cards if company_title_key(c) and company_title_key(c)[0] == company]
        matches = [c for c in related if 'opportunity:' + c['id'] in ids or c.get('job_id') in ids or c.get('url', '').rstrip('/') in urls]
        status = 'fine' if matches else ('broken' if plausible else 'no card')
        counts[status] += 1
        row = {'company': app.get('company'), 'title': app.get('title'), 'url': app.get('url'), 'status': status,
               'matches': [{'title': c.get('title'), 'source': c.get('sources') or urlsplit(c.get('url') or '').hostname,
                            'url': c.get('url'), 'reason': index.reason(c)} for c in matches]}
        if not matches: row['plausible_cards'] = [{'title': c.get('title'), 'url': c.get('url')} for c in plausible]
        # A title-only match with multiple distinct explicit requisitions is ambiguous.
        pair = company_title_key(app)
        variants = {ats_keys(c) for c in plausible if company_title_key(c) == pair and ats_keys(c)}
        title_matches = [m for m in row['matches'] if m['reason'] == 'company_title']
        hosts = Counter(urlsplit(m['url'] or '').hostname for m in title_matches)
        row['doubtful_company_title_matches'] = [m for m in title_matches
            if len(variants) > 1 or hosts[urlsplit(m['url'] or '').hostname] > 1]
        if row['doubtful_company_title_matches']:
            row['doubt_reason'] = 'Multiple same-title source postings may represent separate requisitions; no comparable ATS IDs to disambiguate.'
        results.append(row)
    return {'totals': {s: counts[s] for s in ('broken', 'fine', 'no card')}, 'applications': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('.local/personal'))
    args = parser.parse_args()
    print(json.dumps(audit(args.workspace), indent=2, ensure_ascii=True))
