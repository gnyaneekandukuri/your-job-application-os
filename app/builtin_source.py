"""Built In's public, country-filtered Product Manager search."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

BASE = 'https://builtin.com'
PATH = '/jobs/product-management'
MAX_PAGES = 50
MAX_DETAILS = 1000


def collect_builtin(client, fetch, detail, parser_type, relevant, countries, stop=None):
    from .discovery_recovery import source_window
    days = (source_window.get() or {}).get('days', 1)
    from .discovery_location import COUNTRIES
    codes = {c['code']: c['alpha3'] for c in COUNTRIES}
    if not countries or any(c not in codes for c in countries):
        raise ValueError('Choose supported preferred countries before scanning Built In')
    paths, seen, warnings = [], set(), []
    pages = 0
    for country in dict.fromkeys(countries):
        params = dict(search='product manager', daysSinceUpdated=str(days),
                      country=codes[country], allLocations='true')
        number = 1
        page_signatures = set()
        while True:
            if stop and stop.is_set():
                warnings.append('Built In collection interrupted; results are incomplete.')
                break
            if pages >= MAX_PAGES:
                warnings.append('Reached Built In page safety limit; more search results remain unchecked.')
                break
            query = {**params, **({'page': number} if number > 1 else {})}
            raw = fetch(client, BASE + PATH + '?' + urlencode(query))
            parser = parser_type(); parser.feed(raw)
            # Do not mistake a challenge/login page for an empty completed search.
            if 'searchJobsInput' not in raw or 'postedDateDropdownButton' not in raw:
                raise ValueError('Built In search controls missing; filtered search could not be verified')
            pages += 1
            found = list(dict.fromkeys(urlsplit(link).path for link in parser.links
                         if urlsplit(link).netloc in ('', 'builtin.com')
                         and re.fullmatch(r'/job/[^/?#]+/\d+', urlsplit(link).path)))
            signature = tuple(sorted(found))
            if signature and signature in page_signatures:
                warnings.append('Built In repeated a results page; pagination stopped.')
                break
            page_signatures.add(signature)
            for path in found:
                if relevant(path.replace('-', ' ')) and path not in seen:
                    seen.add(path); paths.append(path)
            next_pages = []
            for link in parser.links:
                parsed = urlsplit(link)
                if parsed.netloc not in ('', 'builtin.com') or parsed.path != PATH:
                    continue
                value = parse_qs(parsed.query).get('page', ['1'])[0]
                if value.isdigit() and int(value) > number:
                    next_pages.append(int(value))
            if not next_pages:
                if 'id="pagination"' not in raw:
                    warnings.append('Built In pagination could not be verified; coverage is partial.')
                break
            number = min(next_pages)
    if len(paths) > MAX_DETAILS:
        warnings.append(f'Built In returned {len(paths)} matching links; only {MAX_DETAILS} details checked.')
    rows = []
    for path in paths[:MAX_DETAILS]:
        if stop and stop.is_set():
            warnings.append('Built In detail collection interrupted; results are incomplete.')
            break
        try:
            row = detail(client, BASE + path)
            rows.append({**row, 'source_id': path.rsplit('/', 1)[-1], 'url': BASE + path,
                         'date_basis': 'source_datePosted', 'funding': 'Unknown'})
        except Exception as exc:
            from .discovery_network import diagnose
            warnings.append({'listing': path, **getattr(exc, 'discovery_diagnostic', diagnose(exc))})
    coverage = (f'Product Manager search; countries: {", ".join(codes[c] for c in dict.fromkeys(countries))}; '
                f'all workplace types; updated within {"one day" if days == 1 else str(days) + " days"}. {pages} search pages, '
                f'{len(paths)} matching links, {len(rows)} details read. '
                'Update recency does not establish original posting age; detail posting dates are preserved. '
                + ('Partial search coverage; see warnings.' if warnings else 'All returned search pages checked.'))
    return rows, coverage, warnings
