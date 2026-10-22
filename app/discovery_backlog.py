"""Deterministic backlog ordering and estimates shared by scans and the CLI."""
from .job_retention import age_state
from .cost_tracking import token_cost

DESCRIPTION_LIMIT = 50
SCREENING_LIMIT = 200
HOST_DELAY = 5


def priority(item, current=None):
    active = age_state(item, current) != 'older_than_four_days' or item.get('kept', False)
    # Sorting descending puts active/unknown dates first, then newest discoveries.
    return active, item.get('first_seen', ''), item.get('id', '')


def estimate(screening, descriptions, *, input_rate, tokens=6000, jd_tokens=2000):
    embedding, _ = token_cost('text-embedding-3-small', jd_tokens, 0, {})
    per_job = tokens * input_rate / 1e6 + embedding
    return {'screening_jobs': screening, 'missing_descriptions': descriptions,
            'estimated_usd': round(screening * per_job, 4),
            'estimated_minutes': round((screening * 2 + descriptions * 10) / 60, 1),
            'slow_request_scenario_minutes': round((screening * 70 + descriptions * (75 + HOST_DELAY)) / 60, 1),
            'assumptions': f'{tokens} Jev input tokens/job at ${input_rate}/million; {jd_tokens} embedding tokens/job at $0.02/million; '
                           '2 seconds/screen and 10 seconds/description typical. Cache reuse lowers cost. '
                           'Description success can add screening jobs; shared context embedding cost is excluded. '
                           'No advisory classification or deep assessment is included.'}
