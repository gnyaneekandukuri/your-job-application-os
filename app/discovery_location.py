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
    city_country = CITY_COUNTRIES.get(text.casefold()) if not found else None
    if city_country:
        found.add(city_country)
    ambiguous = bool(re.search(r"\b(except|excluding|outside|not|non)[ -]", text, re.I))
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
