"""API embeddings are cached locally; searching stored vectors has no API fee."""
from .retrieval import EMBED_MODEL, RetrievalError, digest, now
from .relevance import first_stage
from .cost_tracking import usage_scope

INDEX_VERSION = 'original-excerpts-v1:' + EMBED_MODEL + ':1536'


def embedding_key(text):
    return 'embedding-text:' + EMBED_MODEL + ':1536:' + digest(text.strip())


def prepare_source(service):
    from .jev_assessment import excerpt_units
    _, sha, chunks, fingerprint = service.snapshot()
    previous = service.get('index:' + fingerprint)
    if previous and previous.get('semantic_version') == INDEX_VERSION:
        return
    if previous:
        service.seed_passage_embeddings(previous)
    units = excerpt_units([{**c, 'source_title': service.source_label} for c in chunks])
    with usage_scope(None, 'context_embedding'):
        service.incremental_embeddings([u['text'] for u in units])
    if service.snapshot()[1] != sha:
        raise RetrievalError('Source changed during preparation. Re-prepare its current version.', 409)
    service.put('index:' + fingerprint, {'created_at': now(), 'source_sha256': sha,
                'semantic_version': INDEX_VERSION, 'context_policy': 'ORIGINAL_TEXT', 'chunks': chunks})


def rank_units(engine, units, query):
    if not units:
        return []
    indexed = []
    for u in units:
        vector = engine.get(embedding_key(u['text']))
        if vector is None:
            raise RetrievalError('Prepare the changed experience before semantic assessment. No keyword-only fallback was used.', 409)
        indexed.append({**u, 'raw_vector': vector})
    vector = engine.incremental_embeddings([query])[0]
    orders = first_stage(indexed, query, vector)
    # Reserve semantic-only seats: a lexical distractor must not crowd out
    # every differently worded experience from the bounded shortlist.
    return list(dict.fromkeys(orders['semantic'][:6] + orders['hybrid'] + orders['semantic']))
