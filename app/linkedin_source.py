"""Bounded public LinkedIn search. No account, private API, or browser cookies."""
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, urlunsplit


class Cards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.stack, self.card = [], [], None
        self.card_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            return
        classes = (attrs.get('class') or '').split()
        field = None
        if 'job-search-card' in classes and self.card is None:
            urn = attrs.get('data-entity-urn', '')
            match = re.fullmatch(r'urn:li:jobPosting:(\d+)', urn)
            if match:
                self.card = {'source_id': match[1], 'title': '', 'company': '', 'location': '', 'date_label': '', 'posted_at': None}
                self.card_depth = len(self.stack)
        if self.card is not None:
            for css, name in [('base-search-card__title', 'title'), ('base-search-card__subtitle', 'company'), ('job-search-card__location', 'location')]:
                if css in classes:
                    field = name
            if tag == 'time':
                field = 'date_label'
                self.card['posted_at'] = attrs.get('datetime')
            if tag == 'a' and 'base-card__full-link' in classes:
                url = urlsplit(attrs.get('href', ''))
                if url.scheme == 'https' and url.hostname == 'www.linkedin.com' and url.path.startswith('/jobs/view/'):
                    self.card['url'] = urlunsplit((url.scheme, url.netloc, url.path, '', ''))
        self.stack.append((tag, field))

    def handle_data(self, data):
        if self.card is not None:
            field = next((field for _, field in reversed(self.stack) if field), None)
            if field:
                self.card[field] += data

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        del self.stack[index:]
        if self.card is not None and len(self.stack) <= self.card_depth:
            for field in ('title', 'company', 'location', 'date_label'):
                self.card[field] = ' '.join(self.card[field].split())
            if self.card.get('url') and self.card['title'] and self.card['company']:
                self.rows.append(self.card)
            self.card = None


# LinkedIn job IDs only increase. Reposted and "Promoted by hirer" jobs keep their
# original ID but show a fresh posting age, so an ID far below the newest one in the
# same search is an old job. ~350k IDs/day (measured Oct 2026), so this is ~30 days.
REPOST_ID_GAP = 10_500_000


def reposted(source_id, newest):
    return str(source_id).isdigit() and newest - int(source_id) > REPOST_ID_GAP


def age_bounds(label, observed):
    """Keep a range for rounded ages; never invent an exact publication time."""
    match = re.fullmatch(r'(\d+)\s+(minute|hour|day|week)s?\s+ago', label.lower())
    if not match:
        return {}
    unit = {'minute': 60, 'hour': 3600, 'day': 86400, 'week': 604800}[match[2]]
    lower = int(match[1]) * unit
    upper = lower + unit
    return {'fresh_until': (observed + timedelta(seconds=86400-upper)).isoformat(),
            'older_after': (observed + timedelta(seconds=86400-lower)).isoformat(),
            'age_observed_at': observed.isoformat()}


def collect_linkedin(client, fetch, countries, stop=None):
    from .discovery_sources import relevant, listing_freshness
    from .discovery_location import COUNTRIES
    names = {country['code']: country['name'] for country in COUNTRIES}
    if not countries or any(code not in names for code in countries):
        raise ValueError('Choose a supported country in your context preferences before scanning LinkedIn')
    rows, warnings, seen = [], [], set()
    skipped = 0
    for code in countries[:3]:
        if stop and stop.is_set():
            break
        query = urlencode({'keywords': 'product manager', 'location': names[code], 'f_TPR': 'r86400', 'sortBy': 'DD'})
        raw = fetch(client, 'https://www.linkedin.com/jobs/search/?' + query)
        # A login/challenge page must not be treated as a successful empty search.
        if 'Past 24 hours filter is currently applied' not in raw:
            raise ValueError('LinkedIn past-24-hours search could not be verified; login or search format may have changed')
        parser = Cards(); parser.feed(raw)
        if not parser.rows:
            raise ValueError('LinkedIn returned no readable job cards; empty results are not verified')
        observed = datetime.now(timezone.utc)
        newest = max(int(row['source_id']) for row in parser.rows)
        for row in parser.rows[:60]:
            if row['source_id'] in seen or not relevant(row['title']):
                continue
            if reposted(row['source_id'], newest):
                skipped += 1
                continue
            seen.add(row['source_id'])
            item = {**row, **age_bounds(row['date_label'], observed), 'description': '',
                         'date_basis': 'source_relative_age', 'funding': 'Unknown',
                         'search_country': code, 'source_filter': 'past_24_hours'}
            if listing_freshness(item, observed) != 'older':
                rows.append(item)
    if skipped:
        warnings.append(f'Skipped {skipped} reposted or promoted LinkedIn jobs created more than about 30 days ago.')
    if len(countries) > 3:
        warnings.append('Only the first three preferred countries were searched.')
    return rows, ('Partial coverage: first 60 public Product Manager results per preferred country; '
                  'LinkedIn Past 24 hours filter verified. Rounded posting ages are preserved; '
                  'reposts and original employer dates are not verified.'), warnings
