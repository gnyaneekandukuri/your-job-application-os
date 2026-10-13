from app.discovery_screening import retrieve, query_parts, substantive

LONG = 'Led customer discovery with analysts and shipped a governed reporting workflow that cut weekly preparation time. '


class Engine:
    def incremental_embeddings(self, texts):
        return [[1.0, 0.0] for _ in texts]


def unit(i, passage, text):
    return {'id': f'u{i}', 'passage_id': passage, 'text': text, 'source_title': passage}


def test_titles_are_excluded_resume_is_capped_and_stories_are_preferred():
    units = [unit(0, 'current-resume:1', 'AI Product Manager'),
             *[unit(i, 'current-resume:1', LONG + str(i)) for i in range(1, 5)],
             *[unit(i, 'story-a', LONG + str(i)) for i in range(5, 9)],
             unit(9, 'story-b', LONG + 'b'), unit(10, 'story-c', LONG + 'c')]
    picked = retrieve(Engine(), {'title': 'PM', 'description': 'Own reporting workflows.'}, units, [[1.0, 0.0]] * len(units))
    assert len(picked) == 5 and all(substantive(p) for p in picked)
    assert sum(p['passage_id'].startswith('current-resume:') for p in picked) <= 2
    assert sum(p['passage_id'] == 'story-a' for p in picked) <= 2
    assert {'story-a', 'story-b', 'story-c'} <= {p['passage_id'] for p in picked}


def test_anchor_stories_and_matched_skills_break_ties():
    units = [unit(0, 'story-a', LONG), unit(1, 'story-b', LONG.replace('reporting', 'SQL reporting'))]
    picked = retrieve(Engine(), {'title': 'PM', 'description': 'x'}, units, [[1.0, 0.0]] * 2, skills=['SQL'])
    assert picked[0]['passage_id'] == 'story-b'
    picked = retrieve(Engine(), {'title': 'PM', 'description': 'x'}, units, [[1.0, 0.0]] * 2, anchors=('story-a',))
    assert picked[0]['passage_id'] == 'story-a'


def test_jd_is_matched_by_sections_not_fixed_slices():
    parts = query_parts('<ul><li>Own pricing</li><li>Run discovery</li></ul>\nAbout us: ' + 'benefits ' * 300)
    assert parts[0].startswith('Own pricing') and all(len(p) <= 1800 for p in parts)


def test_story_cap_counts_paragraphs_of_one_story_together():
    units = [{'id': f'u{i}', 'passage_id': f'p{i}', 'text': LONG + str(i), 'source_title': 'Modern story'} for i in range(4)]
    units += [{'id': 'u9', 'passage_id': 'p9', 'text': LONG + 'x', 'source_title': 'Amazon story'}]
    picked = retrieve(Engine(), {'title': 'PM', 'description': 'x'}, units, [[1.0, 0.0]] * len(units))
    assert sum(p['source_title'] == 'Modern story' for p in picked) == 2 and len(picked) == 3
