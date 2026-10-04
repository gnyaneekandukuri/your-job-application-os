"""Company and newsletter leads, deliberately separate from assessed job listings."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from .community_sources import Scripts
from .discovery_sources import canonical, fetch
from .discovery_network import BoardClient, diagnose

SOURCES = {
    'breakout': {'name': 'Breakout List', 'url': 'https://breakoutlist.com/', 'kind': 'Companies'},
    'ramp': {'name': 'Ramp vendor reports', 'url': 'https://ramp.com/data/', 'kind': 'Reports'},
    'nextplay': {'name': 'Next Play', 'url': 'https://nextplayso.substack.com/feed', 'kind': 'Newsletter'},
}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None


def parse_leads(source, raw):
    rows = []
    if source == 'breakout':
        parser = Scripts(); parser.feed(raw)
        found = False
        for script in parser.scripts:
            try:
                data = json.loads(script)
            except ValueError:
                continue
            graph = data.get('@graph', []) if isinstance(data, dict) else []
            for group in graph:
                if group.get('@type') != 'ItemList':
                    continue
                found = True
                for entry in group.get('itemListElement', []):
                    company = entry.get('item', {})
                    if company.get('@type') == 'Organization' and company.get('name') and company.get('url'):
                        rows.append({'title': company['name'], 'url': canonical(company['url']),
                                     'published_at': None, 'source_url': SOURCES[source]['url']})
        if not found:
            raise ValueError('Breakout company directory format changed')
    elif source == 'ramp':
        parser = Links(); parser.feed(raw)
        seen = set()
        for href, label in parser.links:
            url = urljoin(SOURCES[source]['url'], href)
            parsed = urlsplit(url)
            if parsed.hostname != 'ramp.com' or not parsed.path.startswith('/data/top-saas-vendors-on-ramp-'):
                continue
            url = canonical(url)
            if url not in seen:
                rows.append({'title': ' '.join(label.split()) or parsed.path.rsplit('/', 1)[-1].replace('-', ' '),
                             'url': url, 'source_url': SOURCES[source]['url'], 'published_at': None})
                seen.add(url)
        if not rows:
            raise ValueError('Ramp vendor report links were not found')
    elif source == 'nextplay':
        root = ElementTree.fromstring(raw)
        if root.tag != 'rss' or root.find('channel') is None:
            raise ValueError('Next Play RSS format changed')
        for item in root.findall('./channel/item')[:30]:
            title, link = item.findtext('title'), item.findtext('link')
            if not title or not link:
                continue
            date = None
            try:
                date = parsedate_to_datetime(item.findtext('pubDate')).isoformat()
            except (TypeError, ValueError):
                pass
            rows.append({'title': title, 'url': canonical(link), 'source_url': SOURCES[source]['url'],
                         'published_at': date})
    else:
        raise ValueError('Unknown lead source')
    return rows[:150]


def connect(directory):
    db = sqlite3.connect(directory / 'source-leads.sqlite3', timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT)')
    return db


def view(directory):
    with closing(connect(directory)) as db:
        saved = {r['id']: json.loads(r['payload']) for r in db.execute('SELECT * FROM sources')}
    return [{'id': key, **meta, **saved.get(key, {'state': 'not_checked', 'items': []})}
            for key, meta in SOURCES.items()]


def refresh(directory, stop=None, reader=fetch):
    previous = {s['id']: s for s in view(directory)}
    with BoardClient(stop=stop, timeout=20, follow_redirects=False, trust_env=False) as client:
        for source, meta in SOURCES.items():
            if stop and stop.is_set():
                break
            checked = datetime.now(timezone.utc).isoformat()
            try:
                items = parse_leads(source, reader(client, meta['url']))
                data = {'state': 'checked', 'checked_at': checked, 'last_success': checked, 'items': items}
            except Exception as exc:
                data = {'state': 'error', 'checked_at': checked,
                        'last_success': previous[source].get('last_success'),
                        'items': previous[source].get('items', []), 'error': diagnose(exc)['reason']}
            with closing(connect(directory)) as db, db:
                db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)', (source, json.dumps(data)))
    return view(directory)
