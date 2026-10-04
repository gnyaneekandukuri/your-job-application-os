"""Cached semantic retrieval followed by one bounded Jev shortlist check."""
import json
from pathlib import Path
from .jev_client import JevClient
from .cost_tracking import opportunity_scope, usage_scope, scan_id
from .retrieval import digest, RetrievalError, normalized
from .jev_assessment import excerpt_units
from .discovery_preferences import matches as exact_matches

POLICY = json.loads((Path(__file__).resolve().parents[1]/'criteria/discovery-screening.json').read_text())
VERSION = POLICY['version'] + ':' + digest(json.dumps(POLICY, sort_keys=True))


def candidate_profile(view, preferences, passages=None):
    skills = [{'id': s['key'], 'name': s['name'], 'aliases': s.get('aliases', [])}
              for s in view.get('skills', []) if not s.get('hidden')]
    passages = passages or []
    return {'skills': skills, 'current': bool(passages), '_passages': passages,
            'context_version': digest(json.dumps(passages, sort_keys=True)),
            'preferences': preferences,
            'target': {'roles': preferences.get('target_roles', []), 'keywords': []}}


def inputs(item, profile):
    state = {'job': {k: item.get(k, '') for k in ('title', 'description')},
             'preferences': profile.get('preferences', {'target_roles': profile['target']['roles']})}
    key = 'discovery-screen:' + digest(json.dumps([VERSION, state, profile.get('context_version'),
              profile.get('current'), profile.get('skills'), item.get('location_match')], sort_keys=True))
    return state, key


def priority_for(answer):
    label = answer['choice']
    if label == 'needs_information': return 'worth_checking'
    if answer.get('provider_inconsistent') or answer['confidence'] < POLICY['decisive_confidence']:
        return 'worth_checking'
    return label if label in ('high', 'low') else 'worth_checking'


def query_parts(text):
    size = POLICY['jd_part_characters']
    return [text[i:i+size] for i in range(0, len(text), size)]


def retrieve(engine, item, units, vectors):
    query = item['title']+'\n'+item['description']
    if len(query.encode()) > POLICY['max_jd_bytes']:
        raise RetrievalError('Job description is too large for initial matching. Review this role manually.', 409)
    parts = query_parts(query)
    with usage_scope(None, 'discovery_jd_embedding'):
        query_vectors = engine.incremental_embeddings(parts)
    vector = normalized([sum(v[i]*len(part) for v, part in zip(query_vectors, parts))/sum(map(len, parts))
                         for i in range(len(query_vectors[0]))])
    if any(len(v) != len(vector) for v in vectors):
        raise RetrievalError('Candidate embedding dimensions changed. Prepare context again.', 409)
    scores = [sum(a*b for a,b in zip(vector, v)) for v in vectors]
    order = sorted(range(len(units)), key=lambda i: (-scores[i], units[i]['id']))
    return [{**units[i], 'similarity': scores[i]} for i in order[:POLICY['retrieved_passages']]]


def screen(engine, items, profile, stop):
    counts = {'classified': 0, 'cached': 0, 'needs_description': 0, 'errors': 0}
    units = vectors = client = None
    for item in items:
        if stop.is_set(): break
        if item.get('location_match', {}).get('status') == 'outside': continue
        state, key = inputs(item, profile)
        if len(item.get('description', '').strip()) < 200:
            engine.put(key, {'state': 'needs_information', 'reason': 'Add the full job description to match this role.'})
            counts['needs_description'] += 1
            continue
        if not profile.get('current'):
            counts['needs_profile'] = counts.get('needs_profile', 0)+1
            continue
        if engine.get(key):
            counts['cached'] += 1
            continue
        try:
            if len((item['title']+'\n'+item['description']).encode()) > POLICY['max_jd_bytes']:
                raise RetrievalError('Job description exceeds the initial matching budget. No request was sent.', 409)
            if units is None:
                units = excerpt_units(profile['_passages'])
                if not units:
                    raise RetrievalError('Add experience or a resume before matching jobs.', 409)
                if sum(len(u['text'].encode()) for u in units) > POLICY['max_context_bytes']:
                    raise RetrievalError('Candidate context exceeds the initial matching budget. No partial profile was used.', 409)
                # Existing story vectors are reused. New resume excerpts are
                # prepared once here, never by a page refresh or per job.
                token = scan_id.set(None)
                try:
                    with opportunity_scope(None), usage_scope(None, 'context_embedding'):
                        vectors = [normalized(v) for v in engine.incremental_embeddings([u['text'] for u in units])]
                finally:
                    scan_id.reset(token)
                client = JevClient(engine)
            with opportunity_scope(item['id']):
                selected = retrieve(engine, item, units, vectors)
                if stop.is_set(): break
                state['retrieved_experience'] = [{k: u.get(k, '') for k in ('source_title','text','source_caveats')} for u in selected]
                body = {'state': state, 'questions': POLICY['questions']}
                if len(json.dumps(body, ensure_ascii=False).encode()) > POLICY['screen_request_bytes']:
                    raise RetrievalError('Job and selected evidence exceed the screening request budget. No Jev request was sent.', 409)
                answer = client.evaluate(None, 'discovery_screening', state, POLICY['questions'])['priority']
            priority = priority_for(answer)
            reason = {'high': 'Specific work in this role connects with your experience and preferences.',
                      'low': 'The work falls outside your stated preferences.',
                      'worth_checking': 'Potentially relevant work; the experience match or role fit needs review.'}[priority]
            if answer['choice'] == 'high' and priority != 'high':
                reason = 'Jev suggests a strong match, but confidence is too low for automatic deep analysis.'
            if answer['choice'] == 'needs_information':
                reason = 'The available job or candidate information needs clarification.'
            matches = [s['name'] for s in profile.get('skills', [])
                       if exact_matches(item['description'], [s['name'], *s.get('aliases', [])])]
            engine.put(key, {'state': 'needs_information' if answer['choice']=='needs_information' else 'classified',
                             'priority': priority, 'reason': reason, 'matches': matches,
                             'answer': answer, 'retrieved': selected, 'policy_version': VERSION})
            counts['classified'] += 1
        except RetrievalError as exc:
            engine.put(key, {'state': 'error', 'error': str(exc)})
            counts['errors'] += 1
            break
    return counts


def annotate(engine, items, profile):
    """Read-only: changed sources, skills or preferences invalidate old decisions."""
    keyed = [(item, inputs(item, profile)[1]) for item in items]
    cached = engine.get_many([key for _, key in keyed])
    for item, key in keyed:
        saved = cached.get(key)
        item['initial_evaluation'] = saved
        complete = saved and saved['state'] == 'classified' and profile.get('current')
        missing = len(item.get('description', '').strip()) < 200
        needs_information = missing or not profile.get('current') or (saved and saved['state']=='needs_information')
        priority = saved['priority'] if complete and not missing else 'worth_checking'
        label = 'Needs information' if needs_information else {'high':'High priority','worth_checking':'Worth checking','low':'Low priority'}[priority] if complete else 'Not screened'
        if missing:
            reason = 'Add the full job description to match this role.'
        elif not profile.get('current'):
            reason = profile.get('error') or 'Add experience or a resume in Your context before matching jobs.'
        elif saved:
            reason = saved.get('reason') or saved.get('error', 'Matching needs review.')
        else:
            reason = 'Semantic matching is pending the next scan.'
        item['keyword_match'] = {'priority': priority, 'label': label, 'reason': reason,
                                 'experience': [{'term': name} for name in (saved.get('matches', []) if complete else [])],
                                 'target': [], 'different_focus': []}
