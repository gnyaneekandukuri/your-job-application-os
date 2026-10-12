import pytest
from app.jev_requirements import spans
from app.retrieval import RetrievalError


def test_html_block_markup_splits_into_exact_source_substrings():
    duty = 'Own the analytics roadmap and partner with data engineering on governed metrics'
    text = '<strong>Responsibilities<br><br></strong><ul>' + ''.join(f'<li>{duty} {n}.</li>' for n in range(40)) + '</ul>'
    assert len(text) > 1200 and '\n' not in text
    blocks = spans(text)
    assert all(b['text'] in text for b in blocks)
    assert any(b['text'] == f'{duty} 3.' for b in blocks)


def test_unsplittable_page_noise_still_stops_for_review():
    with pytest.raises(RetrievalError, match='too long'):
        spans('Country' + 'AlbaniaAlgeriaAndorra' * 80)
