"""Conservative exact-content identity; never merge on a title alone."""
import hashlib
import json
import re
import unicodedata


def identity(item):
    def norm(value):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()
    values = [norm(item.get(k)) for k in ("company", "title", "location", "description")]
    if not all(values) or len(values[-1]) < 200:
        return None
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def group_listings(items):
    groups = {}
    for item in items:
        groups.setdefault(identity(item) or item['id'], []).append(item)
    result = []
    for members in groups.values():
        # Keep an existing assessment/selection as the primary action target.
        primary = max(members, key=lambda r: (bool(r.get('job_id')), bool(r.get('selected_assessment')), r.get('last_seen',''), r['id']))
        row = dict(primary)
        row['duplicate_ids'] = [r['id'] for r in members]
        row['duplicate_job_ids'] = [r['job_id'] for r in members if r.get('job_id')]
        row['listing_urls'] = sorted({s.get('url') for r in members for s in [r, *r.get('sources', [])] if s.get('url')})
        row['sources'] = list({(s['board'], s.get('url','')):s for r in members for s in r.get('sources', [])}.values())
        row['first_seen'] = min(r['first_seen'] for r in members)
        row['last_seen'] = max(r['last_seen'] for r in members)
        for flag in ('kept', 'retention_protected', 'company_watched'):
            row[flag] = any(r.get(flag) for r in members)
        result.append(row)
    return result
