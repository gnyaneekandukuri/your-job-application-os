"""Section-aware lexical ranking; deterministic, inspectable, no paid inference."""
import re


def segments(text):
    for line_number, line in enumerate(text.splitlines(), 1):
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if sentence.strip():
                yield line_number, sentence.strip()


def heading_section(line):
    text = re.sub(r"^[#*\s]+|[:*\s]+$", "", line).lower().replace("’", "'")
    if len(text)>100:
        return None
    patterns = {
        "responsibilities": r"^(?:(?:key|job|your|core) )?responsibilit|^what you(?:'ll| will) (?:do|work|own)|your (?:role|impact|mission)|^(?:the role|the work|about the role|role overview|duties)$",
        "requirements": r"^(?:(?:minimum|preferred|basic|key|your) )?(?:qualifications|requirements)|^what (?:you bring|we(?:'re| are) looking for)|about you|must have|nice to have|who you are",
        "benefits": r"benefits|compensation|what we offer|equal opportunity|perks|salary",
        "company": r"^about (?!the role|you)|^the (?:company|team)$|^our (?:mission|company|team|values)|why (?:us|join)",
    }
    for section, pattern in patterns.items():
        if re.search(pattern,text):
            return section
    return None


def job_passages(description):
    section="unknown"
    for number,line in enumerate(description.splitlines(),1):
        heading=heading_section(line)
        if heading and (len(line.split())<=12 or line.rstrip().endswith(':')):
            section=heading
            # Inline heading text is retained; full sentences are classified below.
            if not re.search(r"[.!?]",line):
                continue
        for _,sentence in segments(line):
            kind=section
            company=bool(re.search(r"\b(our (?:company|platform|mission|customers|clients)|we (?:are|serve|raised)|we're (?:a|an)|founded in|backed by|funding|year.over.year|yoy|processed payment volume)\b",sentence,re.I))
            action=bool(re.search(r"\b(?:you(?:'ll| will)|this role)\b|^(?:[•*\-]\s*)?(?:own|build|lead|define|design|develop|drive|manage|deliver|partner|conduct|owning|building|leading)\b",sentence,re.I))
            if company and not action:
                kind="company"
            elif section=="unknown" and action:
                kind="responsibilities"
            if re.search(r"\b(equal opportunity|paid time off|health insurance|parental leave)\b",sentence,re.I):
                kind="benefits"
            yield {"line":number,"excerpt":sentence,"section":kind,"field":"description"}


def best_job_hit(item,aliases,policy,find):
    candidates=[{"line":1,"excerpt":item.get("title", ""),"section":"title","field":"title"},*job_passages(item.get("description", ""))]
    hits=[]
    for passage in candidates:
        if policy["section_weights"][passage["section"]]==0:
            continue
        hit=find(passage["excerpt"],aliases)
        if hit:
            hits.append({**passage,**hit,"line":passage["line"]})
    return max(hits,key=lambda h:policy["section_weights"][h["section"]],default=None)


def context_hit(text,aliases,find):
    hits=[]
    for number,sentence in segments(text):
        if sentence.startswith('#') or '?' in sentence:
            continue
        if re.search(r"\b(do not|did not|never|no experience|not experienced|not yet|want to learn|aspiring|aspiration|future|proposed|potential|could|would|plan to|hope to)\b",sentence,re.I):
            continue
        hit=find(sentence,aliases)
        if not hit:
            continue
        # Negation is checked near the matching phrase, not across an entire
        # paragraph (e.g. 'machine learning' is a skill, not an aspiration).
        match_at=sentence.lower().find(hit['matched_text'].lower())
        prefix=sentence[max(0,match_at-65):match_at]
        if re.search(r"\b(not|no|without|lack|lacks)\b",prefix,re.I):
            continue
        strength="work" if re.search(r"\b(built|led|owned|launched|delivered|designed|developed|implemented|managed|defined|conducted|shipped|created|integrated|evaluated|added|worked|working|owned|drove)\b",sentence,re.I) else "mention"
        hits.append({**hit,"line":number,"strength":strength})
    return max(hits,key=lambda h:h["strength"]=="work",default=None)


def rank(experience,targets,target,title,concepts,preferences,policy,find,matches):
    role_match=any(find(title,policy.get('role_aliases',{}).get(role.lower(),[role])) for role in target['roles'])
    target_concepts=set()
    for concept,aliases in concepts.items():
        if any(find(' '.join([concept,*aliases]),[term]) for term in target['keywords']):
            target_concepts.add(concept)
    contributions=[]
    for entry in experience:
        term=entry['term']
        tier='generic' if term in policy['generic_concepts'] else 'work' if term in policy['work_concepts'] else 'specific'
        strength='work' if any(s.get('strength')=='work' for s in entry['sources']) else 'mention'
        section=entry['listing']['section']
        points=policy['concept_weights'][tier]*policy['section_weights'][section]*policy['context_weights'][strength]*(policy['target_boost'] if term in target_concepts else 1)
        contributions.append({'term':term,'tier':tier,'section':section,'context_strength':strength,'target_aligned':term in target_concepts,'points':round(points,3)})
    caution=matches(title,preferences['review_focus_terms']+preferences['review_level_terms'])
    caution=[term for term in caution if not any(find(t,[term]) for t in target['roles']+target['keywords'])]
    other=matches(title,preferences['other_function_terms'])
    score=round(sum(c['points'] for c in contributions),3)
    specific=any(c['tier']=='specific' and c['context_strength']=='work' and c['section'] in ('responsibilities','requirements') for c in contributions)
    substantive=sum(c['tier']!='generic' and c['context_strength']=='work' and c['section'] in ('title','responsibilities','requirements') for c in contributions)
    high=role_match and specific and substantive>=policy['minimum_substantive_matches'] and score>=policy['high_threshold'] and not caution
    priority='low' if other and not role_match else 'high' if high else 'worth_checking'
    return {'priority':priority,'label':{'low':'Low priority','high':'High priority','worth_checking':'Worth checking'}[priority],
            'different_focus':list(dict.fromkeys(other+caution)),
            'ranking':{'policy_version':policy['version'],'points':score,'role_title_match':role_match,'specific_work_match':specific,'substantive_matches':substantive,'contributions':sorted(contributions,key=lambda c:-c['points'])}}
