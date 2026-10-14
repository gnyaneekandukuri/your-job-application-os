"""Public board adapters. Fixed hosts, bounded requests, no employer-page fetching."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
from .extraction import JsonLdParser, plain_text
from .discovery_network import BoardClient, recover, diagnose

BOARDS = {'yc': 'Y Combinator', 'builtin': 'Built In', 'a16z': 'a16z',
          'sequoia': 'Sequoia', 'lightspeed': 'Lightspeed', 'linkedin': 'LinkedIn',
          'hn': 'HN Who is hiring', 'lenny': "Lenny’s Jobs"}
UNAVAILABLE = {'wellfound': 'Direct access blocked (403)', 'khosla': 'Direct access blocked (403)',
               'index': 'Direct access blocked (403)', 'welcome_to_the_jungle': 'Collection not verified',
               'cosign': 'Jobs now load only inside the browser from Cosign’s private service; no public listing to read'}
CONSIDER = {'a16z': ('https://portfoliojobs.a16z.com', 'andreessen-horowitz'),
            'sequoia': ('https://jobs.sequoiacap.com', 'sequoia-capital'),
            'lightspeed': ('https://jobs.lsvp.com', 'lightspeed')}


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid listing URL')
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_')
             and k.lower() not in ('source', 'ref', 'lever-source[]')]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/'), urlencode(sorted(query)), ''))


def freshness(value, current=None):
    current = current or datetime.now(timezone.utc)
    if not value:
        return 'unknown'
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            date = datetime.strptime(value, '%Y-%m-%d').date()
            return 'date_only' if date <= current.date() else 'unknown'
        except ValueError:
            return 'unknown'
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None or date > current:
            return 'unknown'
        return 'within_24h' if current - date < timedelta(hours=24) else 'older'
    except (ValueError, TypeError):
        return 'unknown'


def relevant(title):
    title = title.lower()
    return bool(re.search(r'\b(product (?:manager|lead|management)|(?:head|director|vp) of product|(?:head|director|vp),? product|chief product officer)\b', title))


def fetch(client, url, payload=None, headers=None):
    return recover(client, url, lambda: fetch_once(client, url, payload, headers))


def fetch_once(client, url, payload=None, headers=None):
    with client.stream('POST' if payload is not None else 'GET', url, json=payload, headers=headers) as r:
        r.raise_for_status()
        if r.is_redirect:
            raise ValueError('Source redirected; adapter needs review')
        parts, size = [], 0
        for part in r.iter_bytes():
            size += len(part)
            if size > 4_000_000:
                raise ValueError('Source response exceeds 4 MB')
            parts.append(part)
        return b''.join(parts).decode('utf-8', errors='replace')


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.props, self.links = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-page' in attrs:
            self.props = json.loads(attrs['data-page']).get('props')
        if tag == 'a':
            self.links.append(attrs.get('href', ''))


def postings(value):
    if isinstance(value, list):
        for item in value:
            yield from postings(item)
    elif isinstance(value, dict):
        if value.get('@type') == 'JobPosting':
            yield value
        yield from postings(value.get('@graph', []))


def detail(client, url):
    raw = fetch(client, url)
    parser = JsonLdParser()
    parser.feed(raw)
    jobs = [j for block in parser.blocks for j in postings(json.loads(block))]
    if len(jobs) != 1:
        raise ValueError('Expected one JobPosting on detail page')
    j = jobs[0]
    org = j.get('hiringOrganization') or {}
    loc = j.get('jobLocation') or []
    if not isinstance(loc, list): loc = [loc]
    locations = []
    for place in loc:
        address = place.get('address', {}) if isinstance(place, dict) else {}
        locations.append(', '.join(str(address.get(k)) for k in ('addressLocality', 'addressRegion', 'addressCountry') if address.get(k)))
    text = plain_text(raw)
    label = re.search(r'(?:Reposted|Posted|Updated)\s+[^\n]{0,60}', text)
    return {'title': j.get('title', ''), 'company': org.get('name', 'Unknown') if isinstance(org, dict) else str(org),
            'location': '; '.join(locations), 'description': plain_text(j.get('description', ''))[:50000],
            'posted_at': j.get('datePosted'), 'date_label': label.group(0) if label else '',
            'snapshot_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def listing_freshness(item, current=None):
    current = current or datetime.now(timezone.utc)
    if item.get('date_basis') in ('hiring_comment_timestamp', 'unknown', 'source_updated_at'):
        return 'unknown'
    if item.get('date_basis') == 'source_relative_age':
        try:
            if current < datetime.fromisoformat(item['age_observed_at']):
                return 'unknown'
            if current < datetime.fromisoformat(item['fresh_until']):
                return 'within_24h'
            if current >= datetime.fromisoformat(item['older_after']):
                return 'older'
            return 'unknown'
        except (KeyError, ValueError, TypeError):
            return 'unknown'
    return freshness(item.get('posted_at'), current)


def collect(board, stop=None, countries=None):
    rows, warnings = [], []
    if board == 'lenny':
        from .community_sources import collect_lenny
        return collect_lenny(stop)
    with BoardClient(stop=stop, timeout=20, follow_redirects=False, trust_env=False) as client:
        if board == 'hn':
            from .community_sources import collect_hn
            rows, coverage, warnings = collect_hn(client, fetch)
        elif board == 'linkedin':
            from .linkedin_source import collect_linkedin
            rows, coverage, warnings = collect_linkedin(client, fetch, countries, stop)
        elif board in CONSIDER:
            base, board_id = CONSIDER[board]
            page = fetch(client, base + '/jobs')
            token = re.search(r'"csrfToken":"([^\"]+)"', page)
            if not token: raise ValueError('Anonymous session format changed')
            body = {'board': {'id': board_id, 'isParent': True}, 'meta': {'size': 50},
                    'query': {'titlePrefix': 'product', 'postedSince': 'P7D'}, 'grouped': False}
            seen = set()
            for _ in range(5):
                data = json.loads(fetch(client, base + '/api-boards/search-jobs', body, {'X-CSRF-Token': token.group(1)}))
                if data.get('errors') or not isinstance(data.get('jobs'), list): raise ValueError('Search response changed')
                for j in data['jobs']:
                    if not relevant(j.get('title', '')): continue
                    rows.append({'source_id': str(j['jobId']), 'title': j['title'], 'company': j.get('companyName', 'Unknown'),
                                 'location': '; '.join(j.get('locations', [])), 'url': canonical(j['url']),
                                 'description': plain_text(j.get('description', ''))[:50000], 'posted_at': j.get('timeStamp'), 'date_label': 'Board reports past 7 days',
                                 'funding': (j.get('fundingLV') or {}).get('label', 'Unknown'),
                                 'date_basis': 'board_timestamp', 'snapshot_sha256': hashlib.sha256(json.dumps(j, sort_keys=True).encode()).hexdigest()})
                cursor = data.get('meta', {}).get('sequence')
                if not cursor: break
                if cursor in seen: warnings.append('Repeated pagination cursor; scan stopped'); break
                seen.add(cursor)
                body['meta'] = {**data['meta'], 'size': 50}
            else: warnings.append('Reached five-page limit; coverage is partial')
            coverage = 'Product keyword, board past-7-days filter. Board timestamps are not verified original publication dates.'
        elif board == 'yc':
            base = 'https://www.ycombinator.com'
            parser = PageParser(); parser.feed(fetch(client, base + '/jobs/role/product-manager'))
            if not parser.props or not isinstance(parser.props.get('jobPostings'), list): raise ValueError('YC listing format changed')
            jobs = [j for j in parser.props['jobPostings'] if relevant(j.get('title', ''))]
            for j in jobs[:60]:
                path = j['url']
                if not re.fullmatch(r'/companies/[^/?#]+/jobs/[^/?#]+', path): continue
                row = {'source_id': str(j['id']), 'title': j['title'], 'company': j.get('companyName', 'Unknown'),
                       'location': j.get('location', ''), 'url': base + path, 'description': '', 'posted_at': None,
                       'date_basis': 'source_datePosted', 'funding': 'Unknown', 'date_label': ''}
                try:
                    parsed = detail(client, row['url'])
                    # Preserve the board listing's explicit company/location when detail omits them.
                    row.update({k: v for k, v in parsed.items() if v and v != 'Unknown'})
                except Exception as exc: warnings.append({'listing': str(j['id']), **getattr(exc, 'discovery_diagnostic', diagnose(exc))})
                rows.append(row)
            if len(jobs) > 60: warnings.append('Reached 60-detail limit')
            coverage = 'Public PM category page only; full YC coverage is not established. Individual detail dates checked.'
        elif board == 'builtin':
            base, paths = 'https://builtin.com', []
            for number in range(1, 11):
                url = base + '/jobs/product' + (f'?page={number}' if number > 1 else '')
                parser = PageParser(); parser.feed(fetch(client, url))
                for path in parser.links:
                    if re.fullmatch(r'/job/[^/?#]+/\d+', path) and relevant(path.replace('-', ' ')) and path not in paths:
                        paths.append(path)
                if f'/jobs/product?page={number + 1}' not in parser.links: break
            coverage = 'Up to ten public product-category pages and 100 details; partial coverage. Calendar dates and repost labels are preserved.'
            for path in paths[:100]:
                try:
                    row = detail(client, base + path)
                    rows.append({**row, 'source_id': path.rsplit('/', 1)[-1], 'url': base + path,
                                 'date_basis': 'source_datePosted', 'funding': 'Unknown'})
                except Exception as exc: warnings.append({'listing': path, **getattr(exc, 'discovery_diagnostic', diagnose(exc))})
        else: raise ValueError('Unsupported board')
        if client.recovered_requests:
            warnings.append(f'Recovered {client.recovered_requests} requests after temporary connection failures.')
    return rows, coverage, warnings
