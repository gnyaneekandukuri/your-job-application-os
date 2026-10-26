"""Employer-only exclusions from automatic deep analysis; screening stays unchanged."""
import re

REASON = 'Employer set to initial screening only; automatic deep analysis is disabled.'
_NAMES = set()  # Normalized employer names to keep at initial screening only.
_SUFFIX = re.compile(r'\s+(?:incorporated|inc|llc|ltd|limited|corp|corporation|plc)$')


def initial_only(company):
    # Exact normalized employer names avoid excluding unrelated employers whose
    # job descriptions mention Google/AWS or whose names contain a brand word.
    name = re.sub(r'[^a-z0-9]+', ' ', (company or '').casefold()).strip()
    while True:
        stripped = _SUFFIX.sub('', name)
        if stripped == name:
            return name in _NAMES
        name = stripped
