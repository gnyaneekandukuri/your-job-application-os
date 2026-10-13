"""Cached semantic retrieval followed by one bounded Jev shortlist check."""
import json
from pathlib import Path
from .jev_client import JevClient
from .cost_tracking import opportunity_scope, usage_scope, scan_id
from .retrieval import digest, RetrievalError, normalized
from .jev_assessment import excerpt_units
from .discovery_preferences import matches as exact_matches
from .needs_help import description_problem, UNREADABLE

POLICY = json.loads((Path(__file__).resolve().parents[1]/'criteria/discovery-screening.json').read_text())
# Model-input cache identity excludes local routing and documentation changes.
MODEL_POLICY = {k:v for k,v in POLICY.items() if k not in ('decisive_confidence', 'high_confidence', 'notes')}
VERSION = POLICY['version'] + ':' + digest(json.dumps(MODEL_POLICY, sort_keys=True))
# Title levels above a PM / Senior PM target; kept out of POLICY so cached screens stay valid.
TOO_SENIOR = ['staff', 'principal', 'director', 'head of', 'vp', 'vice president', 'chief', 'group product']
LEGACY_VERSION ='semantic-screen-v1:23bbb85d38cface3e3ff7dc29c56d5782635f527c5f26452c5b635b3a94ef1ea'


def candidate_profile(view, preferences, passages=None):
    skills = [{'id': s['key'], 'name': s['name'], 'aliases': s.get('aliases', [])}
              for s in view.get('skills', []) if not s.get('hidden')]
    passages = passages or []
    return {'skills': skills, 'current': bool(passages), '_passages': passages,
            'context_version': digest(json.dumps(passages, sort_keys=True)),
            'preferences': preferences,
            'target': {'roles': preferences.get('target_roles', []), 'keywords': []}}


# Results saved under earlier policies stay readable; saved answers are re-thresholded by priority_for.
PREVIOUS_VERSIONS = ('semantic-screen-v1:8d7413bd6ec77728f3a8ccf9dfad18cb6e658ba988dbe69e4cdd89d068b06dd6', LEGACY_VERSION)
RESUME_SOURCES = ('current-resume:', 'skills-resume:', 'listed-skill:')
MAX_RESUME_PASSAGES = 2
MAX_PER_STORY = 2


def inputs(item, profile, version=VERSION):
    state = {'job': {k: item.get(k, '') for k in ('title', 'description')},
             'preferences': profile.get('preferences', {'target_roles': profile['target']['roles']})}
    key = 'discovery-screen:' + digest(json.dumps([version, state, profile.get('context_version'),
              profile.get('current'), profile.get('skills'), item.get('location_match')], sort_keys=True))
    return state, key


def priority_for(answer):
    label = answer['choice']
    if label == 'needs_information': return 'worth_checking'
    threshold = POLICY['high_confidence'] if label == 'high' else POLICY['decisive_confidence']
    if answer.get('provider_inconsistent') or answer['confidence'] < threshold:
        return 'worth_checking'
    return label if label in ('high', 'low') else 'worth_checking'


def query_parts(text):
    """JD sections (paragraphs or list items) grouped up to the part size, so each responsibility is matched on its own."""
    from .jev_requirements import BLOCK_TAGS
    size, parts, current = POLICY['jd_part_characters'], [], ''
    for line in (piece.strip() for raw in text.splitlines() for piece in BLOCK_TAGS.split(raw)):
        if not line: continue
        while len(line) > size:
            if current: parts.append(current); current = ''
            parts.append(line[:size]); line = line[size:]
        if current and len(current) + len(line) + 1 > size:
            parts.append(current); current = ''
        current = (current + '\n' + line) if current else line
    return parts + ([current] if current else []) or [text[:size]]


def substantive(unit):
    """Titles, headings and short fragments carry no evidence of work; they are local checks, not Jev slots."""
    text = unit['text'].strip()
    return len(text) >= 60 and len(text.split()) >= 9


def is_resume(unit):
    return str(unit.get('passage_id', '')).startswith(RESUME_SOURCES)


def retrieve(engine, item, units, vectors, skills=(), anchors=()):
    query = item['title']+'\n'+item['description']
    if len(query.encode()) > POLICY['max_jd_bytes']:
        raise RetrievalError('Job description is too large for initial matching. Review this role manually.', 409)
    parts = query_parts(query)
    with usage_scope(None, 'discovery_jd_embedding'):
        part_vectors = [normalized(v) for v in engine.incremental_embeddings(parts)]
    if any(len(v) != len(part_vectors[0]) for v in vectors):
        raise RetrievalError('Candidate embedding dimensions changed. Prepare context again.', 409)
    skill_terms = [t.lower() for t in skills]
    scored = []
    for unit, vector in zip(units, vectors):
        if not substantive(unit): continue
        sims = sorted((sum(a*b for a, b in zip(p, vector)) for p in part_vectors), reverse=True)
        # Mostly the best-matching JD section, so boilerplate cannot dilute a specific responsibility.
        score = .7*sims[0] + .3*sum(sims[:3])/len(sims[:3])
        if not is_resume(unit): score += .03  # stories hold the concrete detail Jev needs
        if unit.get('source_title') in anchors: score += .02
        if any(t in unit['text'].lower() for t in skill_terms): score += .02
        scored.append((score, unit['id'], unit))
    picked, per_story, resume = [], {}, 0
    for score, _, unit in sorted(scored, key=lambda x: (-x[0], x[1])):
        story = unit.get('source_title') or unit.get('passage_id')  # cap per story, not per paragraph
        if per_story.get(story, 0) >= MAX_PER_STORY: continue
        if is_resume(unit) and resume >= MAX_RESUME_PASSAGES: continue
        picked.append({**unit, 'similarity': score})
        per_story[story] = per_story.get(story, 0) + 1
        resume += is_resume(unit)
        if len(picked) == POLICY['retrieved_passages']: break
    return picked


def screen(engine, items, profile, stop, force=False):
    counts = {'classified': 0, 'cached': 0, 'needs_description': 0, 'errors': 0}
    units = vectors = client = None
    for item in items:
        if stop.is_set(): break
        if item.get('location_match', {}).get('status') == 'outside': continue
        if exact_matches(item.get('title', ''), TOO_SENIOR):
            counts['too_senior'] = counts.get('too_senior', 0) + 1  # labeled Low locally; no Jev call
            continue
        state, key = inputs(item, profile)
        if description_problem(item.get('description', '')):
            engine.put(key, {'state': 'needs_information', 'reason': 'Add the full job description to match this role.'})
            counts['needs_description'] += 1
            continue
        if not profile.get('current'):
            counts['needs_profile'] = counts.get('needs_profile', 0)+1
            continue
        if not force and any(engine.get(inputs(item, profile, v)[1]) for v in (VERSION, *PREVIOUS_VERSIONS)):
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
            matches = [s['name'] for s in profile.get('skills', [])
                       if exact_matches(item['description'], [s['name'], *s.get('aliases', [])])]
            with opportunity_scope(item['id']):
                selected = retrieve(engine, item, units, vectors, matches, profile.get('_anchors', ()))
                if stop.is_set(): break
                state['matched_skills'] = matches[:40]
                while True:
                    state['retrieved_experience'] = [{k: u.get(k, '') for k in ('source_title','text','source_caveats')} for u in selected]
                    body = {'state': state, 'questions': POLICY['questions']}
                    if len(json.dumps(body, ensure_ascii=False).encode()) <= POLICY['screen_request_bytes']: break
                    if len(selected) <= 3:
                        raise RetrievalError('Job and selected evidence exceed the screening request budget. No Jev request was sent.', 409)
                    selected = selected[:-1]  # long JDs keep at least three passages
                answer = client.evaluate(None, 'discovery_screening', state, POLICY['questions'])['priority']
            priority = priority_for(answer)
            reason = {'high': 'Specific work in this role connects with your experience and preferences.',
                      'low': 'The work falls outside your stated preferences.',
                      'worth_checking': 'Potentially relevant work; the experience match or role fit needs review.'}[priority]
            if answer['choice'] == 'high' and priority != 'high':
                reason = 'Jev suggests a strong match, but confidence is too low for automatic deep analysis.'
            if answer['choice'] == 'needs_information':
                reason = 'The available job or candidate information needs clarification.'
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
    keyed = [(item, [inputs(item, profile, v)[1] for v in (VERSION, *PREVIOUS_VERSIONS)]) for item in items]
    cached = engine.get_many([k for _, keys in keyed for k in keys])
    for item, keys in keyed:
        saved = next((cached[k] for k in keys if cached.get(k)), None)
        # Reapply the current local cutoff to saved provider answers without a call.
        if saved and saved.get('answer'):
            saved = {**saved, 'priority': priority_for(saved['answer'])}
            if saved['priority'] == 'high':
                saved['reason'] = 'Specific work in this role connects with your experience and preferences.'
        item['initial_evaluation'] = saved
        complete = saved and saved['state'] == 'classified' and profile.get('current')
        missing = bool(description_problem(item.get('description', '')))
        needs_information = missing or not profile.get('current') or (saved and saved['state']=='needs_information')
        priority = saved['priority'] if complete and not missing else 'worth_checking'
        label = 'Needs information' if needs_information else {'high':'High priority','worth_checking':'Worth checking','low':'Low priority'}[priority] if complete else 'Not screened'
        senior = exact_matches(item.get('title', ''), TOO_SENIOR)
        if senior:
            # Title-only rule; no model call. Keeps the listing visible but ranked last.
            priority, label = 'low', 'Low priority'
            reason = 'More senior than your target level (' + ', '.join(senior) + ' in title).'
        elif missing:
            reason = UNREADABLE if description_problem(item.get('description', '')) == 'unreadable' else 'Add the full job description to match this role.'
        elif not profile.get('current'):
            reason = profile.get('error') or 'Add experience or a resume in Your context before matching jobs.'
        elif saved:
            reason = saved.get('reason') or saved.get('error', 'Matching needs review.')
        else:
            reason = 'Semantic matching is pending the next scan.'
        item['keyword_match'] = {'priority': priority, 'label': label, 'reason': reason,
                                 'experience': [{'term': name} for name in (saved.get('matches', []) if complete else [])],
                                 'target': [], 'different_focus': []}
