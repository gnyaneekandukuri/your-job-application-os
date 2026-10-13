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


def same_job(item):
    """Identical company, title and description posted for several locations (e.g. one LinkedIn ID per city)."""
    return identity({**item, 'location': 'any'})


def group_listings(items):
    groups = {}
    for item in items:
        groups.setdefault(same_job(item) or item['id'], []).append(item)
    result = []
    for members in groups.values():
        # Keep an existing assessment/selection as the primary action target.
        primary = max(members, key=lambda r: (bool(r.get('job_id')), bool(r.get('selected_assessment')), r.get('last_seen',''), r['id']))
        row = dict(primary)
        row['duplicate_ids'] = [r['id'] for r in members]
        row['duplicate_job_ids'] = [r['job_id'] for r in members if r.get('job_id')]
        row['listing_urls'] = sorted({s.get('url') for r in members for s in [r, *r.get('sources', [])] if s.get('url')})
        row['sources'] = list({(s['board'], s.get('url','')):s for r in members for s in r.get('sources', [])}.values())
        locations = list(dict.fromkeys(r.get('location') for r in members if r.get('location')))
        if len(locations) > 1: row['location'] = '; '.join(locations)
        rank = {'match': 0, 'unclear': 1}
        row['location_match'] = min((r.get('location_match') or {} for r in members), key=lambda m: rank.get(m.get('status'), 2))
        row['first_seen'] = min(r['first_seen'] for r in members)
        row['last_seen'] = max(r['last_seen'] for r in members)
        for flag in ('kept', 'retention_protected', 'company_watched', 'passed'):
            row[flag] = any(r.get(flag) for r in members)
        result.append(row)
    return result


def role_key(item):
    """Company and exact title; used only to recognize a role the candidate already applied to."""
    norm = lambda value: re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()
    company, title = norm(item.get('company')), norm(item.get('title'))
    return (company, title) if company and title else None
