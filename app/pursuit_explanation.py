"""Job-specific presentation from saved evidence; no model calls or new claims."""

def explain(result, run):
    rows = result.get('core_evidence', [])
    strengths, gaps = [], []
    for row in rows:
        assessment = run.get('results', {}).get(row['requirement_id'], {}).get('judgment', {}).get('assessment', {})
        citations = [c for c in row.get('citations', []) if c.get('quote')]
        item = {'requirement_id': row['requirement_id'], 'requirement': row['requirement'],
                'alignment': row['alignment'],
                'tentative': bool(assessment.get('review_required') and not assessment.get('user_confirmed')),
                'citations': [{k:c.get(k) for k in ('source_title','source_path','start_line','quote')} for c in citations[:2]]}
        if row['alignment'] in ('strong', 'partial') and citations:
            strengths.append(item)
        if row['alignment'] != 'strong':
            gaps.append({**item, 'detail': 'Only part of this requirement is supported by the retrieved evidence.' if row['alignment']=='partial' else 'The retrieved evidence does not establish this requirement.'})
    strengths.sort(key=lambda r: (r['alignment']!='strong', r['tentative'], r['requirement_id']))
    gaps.sort(key=lambda r: (r['alignment']!='insufficient_evidence', r['requirement_id']))
    score = result['score']
    summary = f"{score}/100 documented fit. "
    if result['recommendation']=='SKIP':
        summary += 'Explicit preference conflict: ' + '; '.join(b['kind'].replace('_',' ') for b in result.get('blockers', [])) + '.'
    elif strengths:
        summary += 'Evidence supports ' + '; '.join(r['requirement'] for r in strengths[:2]) + '.'
    else:
        summary += 'No cited core-work strengths were established.'
    if gaps:
        summary += ' Main gap: ' + gaps[0]['requirement'] + '.'
    return {'summary':summary, 'strengths':strengths[:2], 'gaps':gaps[:2],
            'core_requirements_count':len(rows), 'core_requirements_with_citations':sum(bool(r.get('citations')) for r in rows),
            'decision_basis':[c for c in result.get('checks', []) if c['label'] in ('Evidence score for applying','Core evidence coverage') or not c['passed']],
            'blockers':result.get('blockers', [])}
