"""Bounded hybrid retrieval for evidence batches; original passages only."""
import json
from .relevance import first_stage
from .retrieval import RetrievalError

VERSION = 'hybrid-batch-v1'
MAX_BYTES = 48000
PER_REQUIREMENT = 4


def select_passages(passages, queries, vectors):
    if len(queries) != len(vectors):
        raise RetrievalError('Missing retrieval query vectors.', 409)
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
                continue
            selected.append(clean); seen.add(p['id'])
    if not selected:
        raise RetrievalError('No original passages fit the evidence context budget. Review source chunk sizes.', 409)
    return selected
