import pytest

from app.requirement_evidence import validate_judgment
from app.retrieval import RetrievalError


class Citation:
    def __init__(self, passage_id, quote): self.passage_id, self.quote = passage_id, quote


class Judgment:
    support, limitation, clarification, fact_ids = 'direct', '', '', []
    def __init__(self, passage_id, quote): self.evidence = [Citation(passage_id, quote)]


def passage(source_class):
    return {'id': 'p1', 'text': 'Drove a 45% increase in data products.', 'source_title': 'Resume', 'source_path': 'resume',
            'source_sha256': 'x', 'source_class': source_class, 'employer': '', 'heading': '', 'start_line': 1, 'end_line': 1}


RULES = {'facts': {'facts': []}}


def test_resume_lines_can_support_a_requirement():
    cited = validate_judgment(Judgment('p1', 'Drove a 45% increase'), [passage('RESUME_CLAIM')], RULES)
    assert cited[0].source_class == 'RESUME_CLAIM'


def test_coaching_material_still_cannot_establish_experience():
    with pytest.raises(RetrievalError, match='Coaching'):
        validate_judgment(Judgment('p1', 'Drove a 45% increase'), [passage('COACHING')], RULES)
