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


def needs_help(item):
    """At most one request per listing, most blocking first."""
    if item.get('completed'):
        return []
    problem = description_problem(item.get('description', ''))
    if problem == 'unreadable':
        return [{'kind': 'replace_jd', 'message': UNREADABLE}]
    if problem == 'missing' and (item.get('description_fetch') or {}).get('state') == 'NEEDS_PASTE':
        return [{'kind': 'paste_jd', 'message': "We couldn't fetch this job's full description. "
                 "Open the listing, copy the description, and paste it here."}]
    if item.get('description_changed'):
        return [{'kind': 'review_jd', 'message': 'This listing changed since you saved it. Review the updated description.'}]
    assessment = item.get('assessment') or {}
    if problem is None and assessment.get('state') == 'NEEDS_ATTENTION':
        return [{'kind': 'review_analysis', 'message': 'Deep analysis needs your review: ' + (assessment.get('error') or 'open the role to see why.')}]
    if problem is None and assessment.get('state') in ('PAUSED', 'ERROR', 'INTERRUPTED'):
        return [{'kind': 'retry', 'message': 'Deep analysis stopped: ' + (assessment.get('error') or 'no reason was recorded.')}]
    return []
