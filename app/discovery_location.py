"""Conservative country matching of board location fields, not work authorization."""
import json
import re
from pathlib import Path

# Country names/codes generated from Windows .NET RegionInfo, not user preferences.
COUNTRIES = json.loads(Path(__file__).with_name("country_names.json").read_text(encoding="utf-8-sig"))
ALIASES = {"US": ["united states", "united states of america", "usa", "u.s.", "u.s.a."],
           "GB": ["united kingdom", "uk", "england", "scotland", "wales"],
           "ZA": ["south africa"], "IN": ["india"]}


def location_match(location, preferences):
    target = set(preferences.get("location_filter", {}).get("countries", []))
    label = preferences.get("location_filter", {}).get("label", ", ".join(sorted(target)))
    text = (location or "").strip()
    found = set()
    for country in COUNTRIES:
        code = country["code"]
        names = [country["name"], *ALIASES.get(code, [])]
        if any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I) for name in names):
            found.add(code)
        # Short codes only as uppercase standalone location segments: IN/CA
        # in ordinary prose or city names must not be interpreted as countries.
        if re.search(r"(?:^|[,;/|(]\s*)" + re.escape(code) + r"\s*(?:$|[;|/)])", text):
            found.add(code)
        if re.search(r"(?<!\w)" + re.escape(country["alpha3"]) + r"(?!\w)", text):
            found.add(code)
    # A state abbreviation paired with a city is ambiguous without country data.
    if "CA" in found and not re.search(r"\bCanada\b", text, re.I) and re.search(r",\s*CA\s*$", text):
        found.discard("CA")
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
    return {"status": status, "reason": reason, "countries": sorted(found), "target_countries": sorted(target)}
