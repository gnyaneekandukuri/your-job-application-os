"""Free, explainable listing triage. No network, LLM, or candidate-fit scoring."""
import os
import hashlib
import json
import re
from pathlib import Path

PREFERENCES = Path(os.environ.get("JOB_REVIEW_DATA_DIR", Path(__file__).resolve().parents[1] / ".local")) / "workspace/knowledge/discovery-preferences.json"


def load_preferences(path=PREFERENCES):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def matches(text, terms):
    padded = " " + normalize(text) + " "
    return [term for term in terms if " " + normalize(term) + " " in padded]


def classify(item, preferences):
    title = item.get("title", "")
    reasons, signals = [], []
    def record(field, terms):
        found = matches(item.get(field, ""), terms)
        if found:
            signals.append({"field": field, "terms": found})
        return found

    role = record("title", preferences["product_role_terms"])
    other = record("title", preferences["other_function_terms"])
    focus = record("title", preferences["preferred_focus_terms"])
    caution = record("title", preferences["review_focus_terms"])
    level = record("title", preferences["review_level_terms"])
    priority = "worth_checking"
    if other:
        priority = "low"
        reasons.append("Title points to a different function: " + ", ".join(other) + ".")
    elif not role:
        reasons.append("Product-manager role is not clear from the title.")
    elif caution or level:
        if caution:
            reasons.append("Focus needs a closer look: " + ", ".join(caution) + ".")
        if level:
            reasons.append("Level needs review against your preferences: " + ", ".join(level) + ".")
    else:
        priority = "high"
        reasons.append("Preferred PM focus in title: " + ", ".join(focus) + "." if focus else "Product-manager title matches your general PM preference.")
    if focus and priority == "worth_checking":
        reasons.append("Also has preferred title signals: " + ", ".join(focus) + ".")

    # Description keywords are supporting clues only. Company boilerplate must
    # not turn an unrelated title into a high-priority product role.
    description_focus = record("description", preferences["preferred_focus_terms"])
    if description_focus:
        reasons.append("Description mentions " + ", ".join(description_focus[:4]) + "; responsibilities still need review.")
    funding = record("funding", preferences["preferred_funding_terms"])
    if funding:
        reasons.append("Listing reports preferred funding stage: " + ", ".join(funding) + ".")
    location = record("location", preferences["preferred_location_terms"])
    if location:
        reasons.append("Preferred location mentioned: " + ", ".join(location) + ".")

    unknowns = ["Location eligibility and work authorization require separate review."]
    if not item.get("description", "").strip():
        unknowns.append("Full responsibilities unavailable; title-based check only.")
    if not item.get("funding") or normalize(item["funding"]) == "unknown":
        unknowns.append("Funding stage unknown.")
    return {
        "priority": priority,
        "label": {"high":"High priority", "worth_checking":"Worth checking", "low":"Low priority"}[priority],
        "reason": reasons[0], "reasons": reasons, "signals": signals,
        "unknowns": unknowns, "method": "local_rules", "preferences_version": preferences["version"],
        "preferences_hash": hashlib.sha256(json.dumps(preferences, sort_keys=True).encode()).hexdigest(),
        "preference_sources": preferences["sources"],
    }
