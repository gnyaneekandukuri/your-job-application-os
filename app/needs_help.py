"""Free checks that turn stuck listings into one plain request for the candidate."""
from functools import lru_cache

from .jev_requirements import spans
from .retrieval import RetrievalError

UNREADABLE = ("This description seems to include page text that isn't part of the job (for example a "
              "country list or site menu). Open the listing and paste just the job description.")


@lru_cache(maxsize=4096)
def description_problem(text):
    """'missing', 'unreadable' or None. Same splitting rules as deep analysis; no model call."""
    text = (text or '').strip()
    if len(text) < 200:
        return 'missing'
    try:
        spans(text)
    except RetrievalError:
        return 'unreadable'
    return None


def ask(kind, issue, todo):
    return [{'kind': kind, 'issue': issue, 'todo': todo, 'message': issue + ' ' + todo}]


def needs_help(item):
    """At most one request per listing, most blocking first."""
    if item.get('completed'):
        return []
    problem = description_problem(item.get('description', ''))
    if problem == 'unreadable':
        return ask('replace_jd', "The saved description includes website text that isn't part of the job "
                   "(such as a country list or menu), so it can't be analyzed.",
                   'Open the listing, copy just the job description, and paste it here.')
    if problem == 'missing' and (item.get('description_fetch') or {}).get('state') == 'NEEDS_PASTE':
        return ask('paste_jd', "We couldn't get the job description from the job board.",
                   'Open the listing, copy the job description, and paste it here.')
    if item.get('description_changed'):
        return ask('review_jd', 'The listing changed after you saved it.',
                   'Review the updated description so analysis uses the current version.')
    assessment = item.get('assessment') or {}
    if problem is None and assessment.get('state') == 'NEEDS_ATTENTION':
        return ask('review_analysis', 'Deep analysis could not finish: ' + (assessment.get('error') or 'reason not recorded.'),
                   'Open the role to see what needs checking.')
    if problem is None and assessment.get('state') in ('PAUSED', 'ERROR', 'INTERRUPTED'):
        return ask('retry', 'Deep analysis stopped before finishing: ' + (assessment.get('error') or 'reason not recorded.'),
                   'Retry the analysis. Completed steps are reused.')
    return []
