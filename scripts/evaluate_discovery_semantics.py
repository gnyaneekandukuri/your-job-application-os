"""Shadow-test whole-JD semantic discovery without changing routing.

Input: private JSON list of {id, title, description, expected?} cases.
Uses cached candidate excerpt vectors. Only --embed-missing permits paid JD
embeddings; generation is disabled. Results and source text stay in .local.
Similarity is not a probability or a validated qualification score.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cost_tracking import usage_scope
from app.jev_assessment import excerpt_units
from app.model_provider import OpenAIProvider
from app.retrieval import RetrievalError, normalized
from app.role_analysis import RoleAnalysis
from app.semantic_context import embedding_key
from app.story_library import StoryLibrary


def query_parts(text):
    # Keep all text; 1,800 Unicode characters stay below the 8 KB API guard.
    return [text[i:i+1800] for i in range(0, len(text), 1800)]


def similarity_ranking(query_vector, units, vectors):
    query = normalized(query_vector)
    if not query or not any(query):
        raise ValueError('Empty query embedding')
    scores = []
    for unit, vector in zip(units, vectors, strict=True):
        vector = normalized(vector)
        if len(query) != len(vector):
            raise ValueError('Incompatible embedding dimensions')
        scores.append((sum(a*b for a, b in zip(query, vector)), unit))
    return sorted(scores, key=lambda pair: (-pair[0], pair[1]['id']))


def evaluate(directory, cases, embed_missing=False):
    if not 1 <= len(cases) <= 60:
        raise ValueError('Use between 1 and 60 cases')
    library = StoryLibrary(directory/'workspace', directory, OpenAIProvider(allow_generation=False))
    engine = library.engine
    units = excerpt_units(RoleAnalysis(engine, library).passages())
    vectors = [engine.get(embedding_key(unit['text'])) for unit in units]
    if not units or any(v is None for v in vectors):
        raise RetrievalError('Candidate context must already have current excerpt embeddings; no automatic rebuild.', 409)
    queries = [case['title']+'\n'+case['description'] for case in cases]
    parts = [query_parts(q) for q in queries]
    missing = list(dict.fromkeys(q for group in parts for q in group if engine.get(embedding_key(q)) is None))
    if any(len(q.encode('utf-8')) > 24000 for q in missing) or sum(len(q.encode('utf-8')) for q in missing) > 250000:
        raise ValueError('Evaluation exceeds the bounded embedding budget')
    before = engine.usage()
    if missing and embed_missing:
        with usage_scope(None, 'discovery_semantic_evaluation'):
            engine.incremental_embeddings(missing)
    results = []
    start = time.perf_counter()
    for case, group in zip(cases, parts):
        query_vectors = [engine.get(embedding_key(part)) for part in group]
        if any(v is None for v in query_vectors):
            results.append({'id': case['id'], 'state': 'missing_cached_embedding'})
            continue
        vector = [sum(v[i]*len(part) for v, part in zip(query_vectors, group))/sum(map(len, group))
                  for i in range(len(query_vectors[0]))]
        ranked = similarity_ranking(vector, units, vectors)
        results.append({**case, 'state': 'evaluated',
                        'best_similarity': ranked[0][0],
                        'top3_mean': sum(score for score, _ in ranked[:3])/min(3, len(ranked)),
                        'matches': [{**unit, 'similarity': score} for score, unit in ranked[:3]]})
    after = engine.usage()
    return {'mode': 'shadow_only_no_routing_changes', 'candidate_excerpts': len(units),
            'local_comparison_seconds': time.perf_counter()-start,
            'new_calls': after['calls']-before['calls'],
            'estimated_cost_delta': after['estimated_cost_usd']-before['estimated_cost_usd'],
            'new_input_tokens': after['input_tokens']-before['input_tokens'],
            'limitation': 'Expected labels are review hypotheses, not hiring outcomes. No priority thresholds validated.',
            'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--embed-missing', action='store_true')
    args = parser.parse_args()
    private = (Path(__file__).resolve().parents[1]/'.local').resolve()
    if not args.output.resolve().is_relative_to(private):
        parser.error('Detailed output must stay in the private .local directory')
    report = evaluate(args.directory, json.loads(args.cases.read_text(encoding='utf-8')), args.embed_missing)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}))
    for row in sorted(report['results'], key=lambda r: -r.get('best_similarity', 0)):
        print(json.dumps({k: row.get(k) for k in ('id', 'title', 'expected', 'state', 'best_similarity', 'top3_mean')}, ensure_ascii=True))
