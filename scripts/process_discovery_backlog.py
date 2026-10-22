"""Preview saved backlog read-only, or explicitly submit ONE bounded repair batch.

Run as: python -m scripts.process_discovery_backlog --data-dir PATH --dry-run
Execution requires --execute and a server with this version loaded. No app startup.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.discovery_backlog import estimate, DESCRIPTION_LIMIT, SCREENING_LIMIT
from app.job_retention import age_state


def preview(data_dir):
    """Only mode=ro SQLite connections and policy reads; no network/model imports."""
    path = Path(data_dir).resolve() / 'job-list.sqlite3'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        snapshot = db.execute('SELECT updated FROM snapshot WHERE id=1').fetchone()
        items = [json.loads(r[0]) for r in db.execute('SELECT payload FROM listings')]
    pending = []
    missing = []
    for item in items:
        if item.get('passed') or item.get('job_id') or item.get('location_match', {}).get('status') == 'outside': continue
        match = item.get('keyword_match', {})
        if match.get('label') == 'Not screened' and match.get('reason') == 'Semantic matching is pending the next scan.': pending.append(item)
        if match.get('label') == 'Needs information' and 'full job description' in match.get('reason', ''): missing.append(item)
    policy = json.loads((Path(__file__).resolve().parents[1] / 'criteria/jev-assessment.json').read_text())
    active = lambda item: item.get('kept') or age_state(item) != 'older_than_four_days'
    rate = policy['input_usd_per_million']
    return dict(mode='dry-run (read-only; no network or model calls)', snapshot_updated=snapshot[0] if snapshot else None,
                counts=dict(pending=len(pending), pending_active=sum(bool(active(i)) for i in pending),
                            missing_description=len(missing), missing_description_active=sum(bool(active(i)) for i in missing)),
                full_backlog_estimate=estimate(len(pending), len(missing), input_rate=rate),
                one_batch_estimate=estimate(min(SCREENING_LIMIT, len(pending)), min(DESCRIPTION_LIMIT, len(missing)), input_rate=rate),
                paid_steps=['OpenAI embeddings for uncached JD sections and changed candidate passages', 'Jev initial screening'],
                free_steps=['Public description fetching with identity validation'],
                notes=['Projection counts can be stale. Execution reselects eligible jobs and honors fetch cooldowns.',
                       'Each batch has a cooperative two-hour deadline; slow requests can leave work for later batches.',
                       'Successful descriptions may add up to one additional screen each; estimates above exclude those screens.',
                       'Each explicit execution processes at most 50 descriptions and 200 pending screens, active/newest first.',
                       'Existing provider errors require explicit rescreening; this command does not retry them or queue deep assessment.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='Private directory, needed for read-only preview')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Default: read counts and estimates only')
    mode.add_argument('--execute', action='store_true', help='Explicitly POST one paid repair batch to the local app')
    args = parser.parse_args(argv)
    if args.execute:
        from urllib.request import Request, urlopen
        request = Request('http://127.0.0.1:8000/api/discovery/backlog', data=b'{}', method='POST',
                          headers={'X-Local-Review':'1', 'Content-Type':'application/json'})
        with urlopen(request, timeout=15) as response:
            print(response.read().decode())
    else:
        if args.data_dir is None: parser.error('--data-dir is required for a read-only preview')
        print(json.dumps(preview(args.data_dir), indent=2))


if __name__ == '__main__':
    main()
