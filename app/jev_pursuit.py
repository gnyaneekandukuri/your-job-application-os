"""Quoted Jev preference signals; existing deterministic pursuit rules decide."""
from .jev_client import choice, confident
from .jev_requirements import spans
from .retrieval import RetrievalError


def interpret(client, job, run, profile):
    from .pursuit import Interpretation, validate_interpretation, PROMPT, CONSTRAINTS, PRIORITIES
    blocks = [b for b in spans(job.description) if len(b['text']) >= 8]
    if len(blocks) > 120:
        raise RetrievalError('Too many JD blocks for a sourced pursuit check. Review the description.', 409)
    by_id = {b['id']:b['text'] for b in blocks}
    questions = {}
    for kind in sorted(CONSTRAINTS):
        criteria = {'unknown':'No explicit applicable personal constraint or no decisive JD evidence. Silence and ambiguity are unknown.'}
        for b in blocks:
            for finding in ('compatible','incompatible'):
                criteria[f'{finding}__{b["id"]}'] = f'The exact text in block {b["id"]} explicitly establishes {finding} with the candidate\'s stated {kind} constraint. Incompatibility requires an explicit personal constraint and an explicit contradiction.'
        questions['constraint_'+kind] = choice(f'Find the strongest sourced conclusion for the {kind} constraint. Follow policy. In particular, generic no sponsorship is not proof of refusal of H-1B transfers. No unstated personal constraint may be invented.', criteria)
    for kind in sorted(PRIORITIES):
        questions['priority_'+kind] = choice(f'Does a specific JD block establish a match with the stated {kind} preference? Missing information is not a negative finding. Do not infer company funding or investors.',
            {'not_established':'No source block establishes the match.', **{b['id']:f'Block {b["id"]} explicitly establishes the {kind} preference match.' for b in blocks}})
    eligible = [r for r in run['requirements'] if r['category'] in ('requirement','responsibility')]
    if not eligible:
        raise RetrievalError('No clear responsibilities or requirements were established. Review the JD classification before pursuit scoring.', 409)
    for r in eligible:
        questions['core_'+r['id']] = choice(f'Is requirement {r["id"]} central to the actual work? Exclude eligibility, physical conditions, credentials and generic boilerplate.',
                                            {'core':'Central product/work responsibility.', 'other':'Not central to the actual work.'})
    answers = client.evaluate(job.id, 'pursuit_interpretation', {'job_description':job.description, 'blocks':blocks,
        'requirements':run['requirements'], 'profile':profile, 'policy':PROMPT}, questions)
    constraints, priorities = [], []
    for kind in sorted(CONSTRAINTS):
        a = answers['constraint_'+kind]
        finding, quote = 'unknown', ''
        if confident(a) and a['choice'] != 'unknown':
            finding, block = a['choice'].split('__')
            quote = by_id[block]
        constraints.append({'kind':kind, 'finding':finding, 'quote':quote,
                            'reason':f'{kind.replace("_", " ")}: {finding}. Compare the quoted JD wording with your saved preference; uncertainty is not a blocker.'})
    for kind in sorted(PRIORITIES):
        a = answers['priority_'+kind]
        match = confident(a) and a['choice'] != 'not_established'
        priorities.append({'kind':kind, 'finding':'match' if match else 'not_established',
                           'quote':by_id[a['choice']] if match else '',
                           'reason':f'{kind.replace("_", " ")}: '+('match supported by the quoted JD block.' if match else 'not established from the saved JD.')})
    core = [r['id'] for r in eligible if confident(answers['core_'+r['id']]) and answers['core_'+r['id']]['choice']=='core']
    if not core:
        raise RetrievalError('Jev could not confidently identify core responsibilities. Evidence scores are saved; pursuit needs review.', 409)
    return validate_interpretation(Interpretation(constraints=constraints, priorities=priorities, core_requirement_ids=core), job, run, profile)
