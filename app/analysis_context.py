"""Bounded hybrid retrieval for evidence batches; original passages only."""
import json
from .relevance import first_stage
from .retrieval import RetrievalError

VERSION = 'hybrid-batch-v2'
MAX_BYTES = 48000
PER_REQUIREMENT = 4


def retrieve_evidence(passages, queries, vectors):
    """Local search boundary, shared by production and offline evaluation.

    Embeddings are supplied by the caller; this function cannot call a provider.
    Search rankings are candidate relevance, never a qualification score.
    """
    if not queries or any(not q.strip() for q in queries) or len(queries) != len(vectors):
        raise RetrievalError('Missing retrieval query vectors.', 409)
    if not passages or len({p['id'] for p in passages}) != len(passages):
        raise RetrievalError('Evidence search needs nonempty, uniquely identified passages.', 409)
    orders = [first_stage(passages, query, vector)['hybrid'] for query, vector in zip(queries, vectors)]
    selected, seen = [], set()
    # Round-robin gives every requirement its strongest hit before adding more.
    for rank in range(PER_REQUIREMENT):
        for order in orders:
            if rank >= len(order): continue
            p = passages[order[rank]]
            if p['id'] in seen: continue
            clean = {k:v for k,v in p.items() if k not in ('raw_vector', 'vector', 'context')}
            if len(json.dumps(selected+[clean],ensure_ascii=False).encode()) > MAX_BYTES:
                if rank == 0:
                    # Do not silently replace a requirement's strongest hit with
                    # unrelated evidence just because another query used the budget.
                    raise RetrievalError('The strongest evidence for every requirement cannot fit the context budget. Split oversized source passages before resuming.', 409)
                continue
            selected.append(clean); seen.add(p['id'])
    if not selected:
        raise RetrievalError('No original passages fit the evidence context budget. Review source chunk sizes.', 409)
    return {'passages': selected, 'trace': {
        'version': VERSION, 'candidate_count': len(passages),
        'selected_bytes': len(json.dumps(selected, ensure_ascii=False).encode()),
        'passage_ids': [p['id'] for p in selected],
        'queries': [{
            'candidate_ids': [passages[i]['id'] for i in order[:PER_REQUIREMENT]],
            'selected_ids': [passages[i]['id'] for i in order[:PER_REQUIREMENT] if passages[i]['id'] in seen],
        } for order in orders],
    }}


def select_passages(passages, queries, vectors):
    return retrieve_evidence(passages, queries, vectors)['passages']
