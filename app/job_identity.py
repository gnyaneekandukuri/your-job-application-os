"""Conservative exact-content identity; never merge on a title alone."""
import hashlib
import json
import re
import unicodedata
from functools import lru_cache


def identity(item):
    # Key by the actual fields, so a changed JD or a distinct opening cannot
    # inherit an old identity. Bounded reuse avoids re-normalizing every JD
    # on each page request while background analysis is running.
    return _identity(tuple(str(item.get(k) or '') for k in
                           ('company', 'title', 'location', 'description')))


@lru_cache(maxsize=4096)
def _identity(fields):
    values = [re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()
              for value in fields]
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


def listing_url(value):
    """Ignore known tracking only; preserve requisition-bearing query parameters."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    try:
        parts = urlsplit(str(value or ''))
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            return ''
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in
                 {'trk', 'trackingid', 'refid', 'gclid', 'fbclid'}]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                           parts.path.rstrip('/'), urlencode(sorted(query)), ''))
    except ValueError:
        return ''


def listing_urls(item):
    return {listing_url(value) for value in
            [item.get('url'), item.get('source_url'), *item.get('listing_urls', []),
             *(source.get('url') for source in item.get('sources', []))]} - {''}
