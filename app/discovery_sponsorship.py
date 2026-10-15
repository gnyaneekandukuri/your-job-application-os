"""Explicit 'no visa sponsorship' statements in a JD; silence and EEO boilerplate never match."""
import html
import re

NEGATIVE = [re.compile(p, re.I) for p in (
    r"\b(?:unable|not able|cannot|can ?not|can't|won't|will not|do(?:es)? not|don't|doesn't|not)\s+(?:to\s+)?(?:currently\s+|presently\s+)?"
    r"(?:offer|provide|support|sponsor|assist)\w*\b[^;\n]{0,50}?\b(?:visas?|sponsor\w*|immigration|work authori[sz]ation|h-?1b)",
    r"\b(?:visa|immigration|employment|h-?1b)?\s*sponsorship\s+(?:is\s+|will\s+|is\s+currently\s+)?(?:not|un)\w*\s+(?:be\s+)?"
    r"(?:available|offered|provided|supported|possible|considered)",
    r"\bnot\s+(?:be\s+)?eligible\s+for\s+(?:\w+\s+){0,2}sponsorship",
    r"\bwithout\s+(?:the\s+)?(?:need\s+(?:for|of)\s+|requiring\s+|requirement\s+(?:for|of)\s+)?(?:current\s+or\s+future\s+|now\s+or\s+in\s+the\s+future\s+)?"
    r"(?:visa\s+|employer\s+|company\s+|immigration\s+)?sponsorship(?!\s+for\s+an?\s+export)",
    r"\b(?:will|do|does|must)\s+not\s+(?:now\s+or\s+in\s+the\s+future\s+)?require\b[^;\n]{0,50}?sponsor",
    r"\bno\s+(?:visa\s+|immigration\s+)?sponsorship\b",
    r"\b(?:must\s+be|required\s+to\s+be|only\s+open\s+to)\s+(?:a\s+)?u\.?s\.?\s+citizens?\b",
    r"\bu\.?s\.?\s+citizenship\s+(?:is\s+)?required\b",
)]
# A JD that refuses new visas but welcomes transfers is not a blocker.
TRANSFER_OK = re.compile(r"\b(?:accept|consider|support|welcome|sponsor)\w*\b[^.;\n]{0,40}\bh-?1b\s+transfers?", re.I)


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or '')))


def no_sponsorship(description):
    """Return {'quote': sentence} when the JD explicitly refuses sponsorship, else None."""
    text = plain(description)
    if TRANSFER_OK.search(text): return None
    for pattern in NEGATIVE:
        match = pattern.search(text)
        if match:
            start = max(text.rfind('.', 0, match.start()) + 1, match.start() - 160)
            end = text.find('.', match.end())
            end = len(text) if end < 0 else min(end + 1, match.end() + 160)
            return {'quote': text[start:end].strip()}
    return None
