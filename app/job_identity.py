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
    """Normalize listing/apply URLs while preserving requisition query parameters."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    try:
        parts = urlsplit(str(value or ''))
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            return ''
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in
                 {'trk', 'trackingid', 'refid', 'gclid', 'fbclid', 'i', 'preference_id',
                  'gh_src', 'source', 'ref', 'lever-source', 'mc_cid', 'mc_eid'}]
        path = re.sub(r'/(?:apply|application)$', '', parts.path.rstrip('/'), flags=re.I).rstrip('/')
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix('www.'),
                           path, urlencode(sorted(query)), ''))
    except ValueError:
        return ''


def listing_urls(item):
    return {listing_url(value) for value in
            [item.get('url'), item.get('source_url'), *item.get('listing_urls', []),
             *(source.get('url') for source in item.get('sources', []))]} - {''}


def ats_keys(item):
    """Explicit requisition IDs; generic query IDs remain scoped to their host."""
    from urllib.parse import urlsplit, parse_qsl
    result = set()
    uuid = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    for url in listing_urls(item):
        p = urlsplit(url)
        query = dict((k.casefold(), v) for k, v in parse_qsl(p.query))
        if query.get('gh_jid', '').isdigit():
            result.add(('gh', query['gh_jid']))
        if p.hostname and (p.hostname == 'greenhouse.io' or p.hostname.endswith('.greenhouse.io')):
            match = re.search(r'/jobs/(\d+)', p.path)
            if match: result.add(('gh', match[1]))
        for host, provider in (('ashbyhq.com', 'ashby'), ('lever.co', 'lever')):
            if p.hostname and (p.hostname == host or p.hostname.endswith('.' + host)):
                match = re.search(uuid, p.path, re.I)
                if match: result.add((provider, match[0].casefold()))
        if p.hostname and (p.hostname == 'comeet.com' or p.hostname.endswith('.comeet.com') or p.hostname == 'comeet.co'):
            match = re.search(r'/([a-z0-9]+\.[a-z0-9]+)(?:/|$)', p.path, re.I)
            if match: result.add(('comeet', match[1].casefold()))
        if query.get('jobid'):
            result.add(('jobid:' + (p.hostname or ''), query['jobid'].casefold()))
    return frozenset(result)


def company_title_key(item):
    def words(value):
        return re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', str(value or '')).casefold()).split()
    company = words(item.get('company'))
    while company and company[-1] in {'inc', 'incorporated', 'llc', 'ltd', 'limited', 'corp', 'corporation'}:
        company.pop()
    title = ['senior' if w == 'sr' else w for w in words(item.get('title'))]
    return (' '.join(company), ' '.join(title)) if company and title else None


class AppliedMatchIndex:
    """Index applications once; each card only visits its company/title bucket."""
    def __init__(self, records):
        self.urls, self.ids, self.pairs = set(), set(), {}
        for record in records:
            item = {**record.get('job_snapshot', {}), **{k: v for k, v in record.items() if v}}
            self.urls.update(listing_urls(item))
            keys = ats_keys(item)
            self.ids.update(keys)
            pair = company_title_key(item)
            # Internal applications keep their existing exact-content behavior.
            # Only external logs need the company/title fallback.
            if pair and ('job_id' not in record or str(record['job_id']).startswith('external:')):
                self.pairs.setdefault(pair, set()).add(keys)

    def reason(self, item):
        if listing_urls(item) & self.urls: return 'url'
        keys = ats_keys(item)
        if keys & self.ids: return 'ats_id'
        for applied in self.pairs.get(company_title_key(item), ()):
            # A shared provider with disjoint IDs means a different requisition.
            common = {p for p, _ in keys} & {p for p, _ in applied}
            if not any({v for p, v in keys if p == provider}.isdisjoint(
                       {v for p, v in applied if p == provider}) for provider in common):
                return 'company_title'
        return None
