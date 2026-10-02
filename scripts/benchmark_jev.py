"""Small live smoke benchmark; synthetic public-style JDs, no personal context.

Run from the product directory: .venv/Scripts/python.exe -m scripts.benchmark_jev
This checks basic boundaries, not production accuracy. Results stay in .local.
"""
import json
import os
from pathlib import Path

from app.discovery_jev import JevClassifier

CASES = [
    ('agent_product', 'Product Manager', 'product', 'ai_ml',
     'Own the product roadmap for autonomous workflows. Interview customers and prioritize capabilities. Define evaluation datasets, tool execution, orchestration and failure recovery. Set success metrics and partner with engineers to ship improvements to agent behavior.'),
    ('ai_company_marketing', 'Product Marketing Manager', 'gtm', 'other',
     'Our company builds cutting-edge AI agents. Your responsibilities: own market positioning and launch messaging, produce sales enablement collateral and run demand generation campaigns. Measure campaign conversion. Product roadmap and model behavior belong to a separate product team.'),
    ('data_product', 'Technical Product Manager', 'product', 'data',
     'Own discovery, roadmap and prioritization for a data platform. Interview analysts, define ingestion and governance requirements, improve pipeline reliability and data quality, and work with engineering to ship self-service datasets. Measure adoption and time to insight.'),
    ('program_title_overlap', 'AI Platform Program Manager', 'project_program', 'ai_ml',
     'Coordinate delivery schedules and dependencies across AI model evaluation teams. Track milestones, risks, staffing and status reporting. Facilitate execution meetings and escalate blockers. Product managers own product discovery, strategy, feature prioritization and the roadmap.'),
    ('platform_product', 'Product Lead', 'product', 'platform',
     'Own strategy and the roadmap for developer APIs and integrations. Conduct customer discovery, prioritize developer workflows and measure API adoption. Partner with engineering and design to ship SDK capabilities and improve the developer experience and platform reliability.'),
    ('sales_ai_company', 'Strategic Partnerships Manager', 'sales', 'other',
     'We sell an AI-powered enterprise platform. In this role, negotiate commercial distribution agreements, manage partner accounts and build a revenue pipeline. Own partner quotas and renewals. Work with product managers to share customer requests, without owning the product roadmap.'),
]


def main():
    if not os.environ.get('TYPESAFE_API_KEY', '').strip():
        raise SystemExit('TypeSafe API key is not configured. Run scripts/connect-jev.ps1 first in this terminal.')
    folder = Path('.local/jev-benchmark')
    folder.mkdir(parents=True, exist_ok=True)
    classifier = JevClassifier(folder/'cache.sqlite3')
    rows = []
    for name, title, function, focus, description in CASES:
        result = classifier.classify({'title': title, 'description': description})
        row = {'case': name, 'expected': {'function': function, 'focus': focus}, 'result': result}
        row['matches'] = all(result.get('answers', {}).get(k, {}).get('choice') == v for k, v in row['expected'].items())
        rows.append(row)
        print(name, 'matches' if row['matches'] else result['state'] if result['state'] != 'classified' else 'DIFFERS')
        if result['state'] == 'error':
            print(result['error'])
            break
    (folder/'results.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(f'{sum(row["matches"] for row in rows)}/{len(CASES)} cases matched both expected labels. This is a smoke test, not a validated accuracy estimate.')


if __name__ == '__main__':
    main()
