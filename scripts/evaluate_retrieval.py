"""Replay retrieval against saved citations; never generates assessments.

Offline by default. --embed-missing explicitly permits bounded query embeddings.
Saved model citations are a regression reference, NOT human-labelled ground truth.
Detailed output is private and belongs in .local, never in Git.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis_context import MAX_BYTES, select_passages
from app.cost_tracking import usage_scope
from app.model_provider import OpenAIProvider
from app.relevance import first_stage, lexical_query
from app.retrieval import EMBED_MODEL, RetrievalError, bm25, digest
from app.role_analysis import RoleAnalysis
from app.story_library import StoryLibrary
from app.user_workspace import default_data_directory


def cached_vector(engine, text):
    return engine.get('embedding-text:' + EMBED_MODEL + ':1536:' + digest(text.strip()))


def replay(directory, limit=80, embed_missing=False):
    library = StoryLibrary(directory / 'workspace', directory, OpenAIProvider())
    engine = library.engine
    passages = RoleAnalysis(engine, library).passages()
    current = {p['id']: p for p in passages}
    with closing(engine.connect()) as db:
        runs = [json.loads(r[0]) for r in db.execute("SELECT payload FROM cache WHERE key LIKE 'role-analysis:%'")]
    with closing(sqlite3.connect(directory / 'reviews.sqlite3')) as db:
        jobs = {r[0]: json.loads(r[1]) for r in db.execute('SELECT id,payload FROM jobs')}
    latest = {}
    for run in sorted(runs, key=lambda r: r.get('updated_at', ''), reverse=True):
        if run.get('state') == 'COMPLETE':
            latest.setdefault(run['job_id'], run)
    cases = []
    excluded = 0
    for job_id, run in latest.items():
        jd = jobs.get(job_id, {}).get('description', '')
        if not jd or digest(jd) != run['source_versions']['jd']:
            continue
        for req in run['requirements']:
            row = run['results'].get(req['id'], {})
            citations = row.get('passages', [])
            if not citations:
                continue
            # Compare only unchanged, exact-source citations, not stale revisions.
            if any(c['passage_id'] not in current or
                   c['source_sha256'] != current[c['passage_id']]['source_sha256'] or
                   c['quote'] not in current[c['passage_id']]['text'] for c in citations):
                excluded += 1
                continue
            cases.append({'job_id': job_id, 'requirement_id': req['id'],
                          'query': req['interpretation'] + '\n' + req['quote'],
                          'quote': req['quote'],
                          'jd_query': jd,
                          'expected': sorted({c['passage_id'] for c in citations})})
        if len(cases) >= limit:
            break
    cases = cases[:limit]
    texts = list(dict.fromkeys(c[k] for c in cases for k in ('query', 'jd_query')))
    missing = [t for t in texts if cached_vector(engine, t) is None]
    if sum(len(t.encode()) for t in missing) > 250000:
        raise ValueError('Query embedding evaluation exceeds 250 KB. Reduce --limit.')
    if embed_missing and missing:
        with usage_scope(None, 'retrieval_evaluation'):
            engine.incremental_embeddings(missing)
    results = []
    missing_cases = 0
    for case in cases:
        vector = cached_vector(engine, case['query'])
        jd_vector = cached_vector(engine, case['jd_query'])
        for name, query in [('lexical_whole_jd_top4', case['jd_query']),
                            ('lexical_original_quote_top4', case['quote']),
                            ('lexical_interpreted_requirement_top4', case['query'])]:
            scores = bm25(lexical_query(query), [p.get('heading', '')+'\n'+p['text'] for p in passages])
            order = sorted((i for i in range(len(passages)) if scores[i] > 0),
                           key=lambda i: (-scores[i], passages[i]['id']))
            selected = []
            for i in order[:4]:
                clean = {k: val for k, val in passages[i].items() if k not in ('raw_vector', 'vector', 'context')}
                if len(json.dumps(selected + [clean], ensure_ascii=False).encode()) <= MAX_BYTES:
                    selected.append(clean)
            expected = set(case['expected'])
            hits = expected & {p['id'] for p in selected}
            results.append({'strategy': name, 'job_id': case['job_id'], 'requirement_id': case['requirement_id'],
                            'citation_recall': len(hits)/len(expected), 'hit': bool(hits),
                            'bytes': len(json.dumps(selected, ensure_ascii=False).encode())})
        if vector is None or jd_vector is None:
            missing_cases += 1
            continue
        for name, query, v, count in [
            ('whole_jd_top4', case['jd_query'], jd_vector, 4),
            ('requirement_top4', case['query'], vector, 4),
            ('requirement_top8', case['query'], vector, 8),
        ]:
            order = first_stage(passages, query, v)['hybrid']
            selected = []
            for i in order[:count]:
                clean = {k: val for k, val in passages[i].items() if k not in ('raw_vector', 'vector', 'context')}
                if len(json.dumps(selected + [clean], ensure_ascii=False).encode()) <= MAX_BYTES:
                    selected.append(clean)
            if count == 4:
                # Exercise the actual production selection boundary.
                selected = select_passages(passages, [query], [v])
            expected = set(case['expected'])
            hits = expected & {p['id'] for p in selected}
            results.append({'strategy': name, 'job_id': case['job_id'], 'requirement_id': case['requirement_id'],
                            'citation_recall': len(hits) / len(expected), 'hit': bool(hits),
                            'bytes': len(json.dumps(selected, ensure_ascii=False).encode())})
    summary = {}
    for name in sorted({r['strategy'] for r in results}):
        rows = [r for r in results if r['strategy'] == name]
        summary[name] = {'cases': len(rows), 'mean_saved_citation_recall': sum(r['citation_recall'] for r in rows)/len(rows),
                         'any_saved_citation_rate': sum(r['hit'] for r in rows)/len(rows),
                         'mean_bytes_per_requirement': round(sum(r['bytes'] for r in rows)/len(rows))}
    return {'reference': 'Previously generated citations, not human-labelled truth. No final-answer quality measured.',
            'eligible_cases': len(cases), 'hybrid_cases_skipped_missing_embeddings': missing_cases,
            'excluded_stale_citation_cases': excluded, 'strategies': summary, 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=80)
    parser.add_argument('--embed-missing', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit must be between 1 and 100')
    directory = default_data_directory()
    try:
        report = replay(directory, args.limit, args.embed_missing)
    except RetrievalError as exc:
        print(str(exc))
        return 1
    output = directory / 'retrieval-evaluation.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'results'}, indent=2))
    print('Private report:', output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
