"""Conservative country matching of board location fields, not work authorization."""
import json
import re
from pathlib import Path

# Country names/codes generated from Windows .NET RegionInfo, not user preferences.
COUNTRIES = json.loads(Path(__file__).with_name("country_names.json").read_text(encoding="utf-8-sig"))
ALIASES = {"US": ["united states", "united states of america", "usa", "u.s.", "u.s.a."],
           "GB": ["united kingdom", "uk", "england", "scotland", "wales"],
           "ZA": ["south africa"], "IN": ["india"]}

# Common board labels for these US metros. Exact full-field matches only:
# never override an explicit foreign country or infer remote eligibility.
CITY_COUNTRIES = {name: 'US' for name in (
    'new york city', 'new york, ny', 'new york city, ny', 'nyc', 'nyc office',
    'san francisco', 'san francisco, ca', 'san francisco office')}


# Keep compiled patterns: the full country set exceeds re's small global cache.
PATTERNS = tuple((country['code'],
    tuple(re.compile(r'(?<!\w)' + re.escape(name) + r'(?!\w)', re.I)
          for name in [country['name'], *ALIASES.get(country['code'], [])]),
    re.compile(r'(?:^|[,;/|(]\s*)' + re.escape(country['code']) + r'\s*(?:$|[;|/)])'),
    re.compile(r'(?<!\w)' + re.escape(country['alpha3']) + r'(?!\w)'))
    for country in COUNTRIES)

# US state labels are country evidence, like a country name. Two-letter codes count
# only in the "City, ST" position: several are also country codes (PA, MA, GA, IN, CO).
US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia',
    'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois',
    'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana',
    'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
    'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon',
    'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia',
    'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming'}
STATE_NAMES = {name.casefold(): code for code, name in US_STATES.items()}
COUNTRY_CODES = {country['code'] for country in COUNTRIES}
COUNTRY_SUFFIX = re.compile(r'\s*,\s*(?:US|USA|U\.S\.A?\.?|United States(?: of America)?)\s*$', re.I)
CITY_STATE = re.compile(r'^[^,]+,\s*([A-Za-z][A-Za-z .]*?)\s*$')
SEGMENTS = re.compile(r'\s*(?:[/;|]|\bor\b)\s*', re.I)
UPPER_US = re.compile(r'(?<![A-Za-z])US(?![A-Za-z])')
GEORGIA_COUNTRY_CITIES = re.compile(r'\b(?:Tbilisi|Batumi|Kutaisi)\b', re.I)
# Major cities whose country code is also a state code: "Berlin, DE" is Germany,
# "Delhi, IN" is India, "Jakarta, ID" is Indonesia, "Toronto, CA" is Canada.
FOREIGN_CITIES = re.compile(r'\b(?:Berlin|Munich|M[uü]nchen|Hamburg|Frankfurt|Cologne|K[oö]ln|Stuttgart|D[uü]sseldorf'
    r'|Delhi|Bengaluru|Bangalore|Mumbai|Hyderabad|Chennai|Pune|Gurugram|Gurgaon|Noida|Kolkata|Ahmedabad'
    r'|Jakarta|Bandung|Surabaya|Toronto|Vancouver|Montr[eé]al|Ottawa|Calgary|Edmonton'
    r'|Bogot[aá]|Medell[ií]n|Buenos Aires|C[oó]rdoba|Tel Aviv|Jerusalem|Haifa|Herzliya|Casablanca|Rabat'
    r'|Tirana|Valletta|Ulaanbaatar|Podgorica|Tunis|Vientiane|Chi[sș]in[aă]u|Macau)\b', re.I)


def us_states(text):
    """State codes named by "City, ST" / "City, State" segments, or a bare state name."""
    found = set()
    foreign_city = FOREIGN_CITIES.search(text)  # Any segment: "Gurugram, HR, IN / DL, IN"
    for segment in SEGMENTS.split(text):
        segment = COUNTRY_SUFFIX.sub('', segment.strip())
        bare = STATE_NAMES.get(segment.casefold())
        if bare and bare != 'GA':  # A bare "Georgia" may be the country.
            found.add(bare)
            continue
        match = CITY_STATE.match(segment)
        if not match:
            continue
        label = match.group(1)
        if label in COUNTRY_CODES and foreign_city:
            continue
        code = label if label in US_STATES else STATE_NAMES.get(label.casefold())
        if code and not (code == 'GA' and GEORGIA_COUNTRY_CITIES.search(segment)):
            found.add(code)
    return found


def location_match(location, preferences):
    target = set(preferences.get("location_filter", {}).get("countries", []))
    label = preferences.get("location_filter", {}).get("label", ", ".join(sorted(target)))
    text = (location or "").strip()
    found = set()
    for code, names, short_code, alpha3 in PATTERNS:
        if any(pattern.search(text) for pattern in names) or short_code.search(text) or alpha3.search(text):
            found.add(code)
    # A state abbreviation paired with a city is ambiguous without country data.
    if "CA" in found and not re.search(r"\bCanada\b", text, re.I) and re.search(r",\s*CA\s*$", text):
        found.discard("CA")
    states = us_states(text)
    if states:
        found.add("US")
        # "Pittsburgh, PA" is Pennsylvania, not Panama, unless the country is also named.
        for code, names, _short, _alpha3 in PATTERNS:
            if code in states and not any(pattern.search(text) for pattern in names):
                found.discard(code)
        if "GA" in states and not GEORGIA_COUNTRY_CITIES.search(text):
            found.discard("GE")
    if UPPER_US.search(text):  # "US-REMOTE", "Remote - US"
        found.add("US")
    city_country = CITY_COUNTRIES.get(text.casefold()) if not found else None
    if not found and not city_country:
        # Accept a list only when EVERY complete segment is a known city label.
        # Never infer US eligibility from an unknown/foreign/remote segment.
        cities = [part.strip().casefold() for part in re.split(r'\s*[/;|]\s*', text)]
        if len(cities) > 1 and all(CITY_COUNTRIES.get(city) == 'US' for city in cities):
            city_country = 'US'
    if city_country:
        found.add(city_country)
    # "not" only signals an exclusion before a place ("not in Canada"), not "not set, USA".
    ambiguous = bool(re.search(r"\b(except|excluding|outside|non)[ -]|\bnot (?:in|available|eligible|open|based)\b", text, re.I))
    if not target or ambiguous:
        status = "unclear"
    elif found & target:
        status = "match"
    elif found:
        status = "outside"
    else:
        status = "unclear"
    reason = {"match": f"Listing includes {label}.", "outside": f"Listed countries are outside {label}.", "unclear": f"Cannot confirm {label} from the location field."}[status]
    if city_country and status != 'unclear':
        reason = f"Recognized city label: {text} → {city_country}. " + reason
    return {"status": status, "reason": reason, "countries": sorted(found), "target_countries": sorted(target)}
