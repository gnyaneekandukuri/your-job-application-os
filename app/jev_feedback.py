"""Select explicit user-supplied configuration changes; never generate new values."""
import json
import re
from .jev_client import choice, confident


def propose(manager, job, reason, configs):
    from .feedback_learning import Proposal, Change, PATHS, lookup, PROMPT
    fields = {f'{target}|{path}':f'{target}: {path}' for target,paths in PATHS.items() for path in paths}
    answers = manager.jev_client().evaluate(job.id, 'feedback_routing', {'reason':reason, 'configuration':configs, 'policy':PROMPT}, {
        'scope':choice('Classify the reason. A request for a higher score is not evidence or grounds to loosen global criteria.',
                       {'role_only':'An exception for this role only.', 'reusable':'An explicit, justified reusable preference or criterion correction.', 'needs_evidence':'New or disputed career evidence.', 'needs_clarification':'Ambiguous or unsupported correction.'}),
        'field':choice('Which one configuration field does the user explicitly want corrected? Select none if it is ambiguous.', {'none':'No unambiguous field', **fields}),
    })
    scope = answers['scope']['choice'] if confident(answers['scope']) else 'needs_clarification'
    changes = []
    if scope == 'reusable' and confident(answers['field']) and answers['field']['choice'] != 'none':
        target,path = answers['field']['choice'].split('|')
        kind = PATHS[target][path]
        old = lookup(configs[target], path)
        values = []
        if kind == 'number':
            values = [int(n) for n in re.findall(r'(?<![\w.])\d{1,3}(?![\w.])', reason) if 0 <= int(n) <= 100]
        elif kind == 'bool':
            values = [True, False]
        else:
            quoted = re.findall(r'"([^"\n]{1,1200})"', reason)
            if quoted:
                values = [list(dict.fromkeys(old+quoted)), quoted]
        values = [v for v in values if v != old]
        if values:
            options = {f'v{i}':v for i,v in enumerate(values)}
            answer = manager.jev_client().evaluate(job.id, 'feedback_value', {'reason':reason, 'field':fields[answers['field']['choice']], 'current':old, 'candidate_values':options, 'policy':PROMPT},
                {'value':choice('Which candidate value is explicitly supported by the user explanation as a reusable correction? Do not invent desired values or loosen scoring solely to obtain a higher score.',
                                {'none':'No supported unambiguous value', **{k:json.dumps(v) for k,v in options.items()}})})['value']
            if confident(answer) and answer['choice'] != 'none':
                changes = [Change(target=target,path=path,value_json=json.dumps(options[answer['choice']]), supporting_quote=reason,
                                  requirement_ids=[], reason='Jev selected this explicit user-supplied correction; approval is required before it affects future roles.')]
    if scope == 'reusable' and not changes:
        scope = 'needs_clarification'
    return Proposal(scope=scope, diagnosis={'role_only':'Keep this decision specific to the role.', 'reusable':'Review the explicit reusable correction below.',
        'needs_evidence':'Review the underlying experience evidence.', 'needs_clarification':'The requested correction needs clarification.'}[scope],
        explanation='Your saved decision does not itself change evidence scores. Proposed changes are selected from explicit values, not generated prose.',
        question='State the preference or criterion to change and its value. Put new role/preference entries in quotation marks.' if scope=='needs_clarification' else '',
        expected_impact='No score change is assumed. Approved reusable changes apply to future assessments; original evidence remains unchanged.', changes=changes)
