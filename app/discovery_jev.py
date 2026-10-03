"""Cached, advisory role classification. Network calls happen only in scan work."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ConfigDict
from .cost_tracking import scan_id

POLICY = Path(__file__).resolve().parents[1] / 'criteria/discovery-role-classification.json'


class Choice(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)
    type: str
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


class JevClassifier:
    def __init__(self, path, post=None):
        self.path = path
        self.post = post or httpx.post
        with closing(self.db()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS jev_classifications (fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS jev_classification_calls (
                id INTEGER PRIMARY KEY, fingerprint TEXT, created_at TEXT, scan_id INTEGER,
                opportunity_id TEXT, model TEXT, state TEXT, input_tokens INTEGER,
                output_tokens INTEGER, cost REAL, error TEXT, latency_ms INTEGER)''')

    def db(self):
        return sqlite3.connect(self.path, timeout=20)

    def status(self):
        return {'configured': bool(os.environ.get('TYPESAFE_API_KEY', '').strip()), 'mode': 'advisory',
                'message': 'Jev labels are being evaluated alongside existing priorities.' if os.environ.get('TYPESAFE_API_KEY', '').strip() else 'Jev needs a TypeSafe API key. Local matching remains active.'}

    def request(self, item):
        policy = json.loads(POLICY.read_text(encoding='utf-8'))
        # Public listing content only: never candidate stories, preferences or credentials.
        body = {'model': policy['model'], 'state': {'title': item.get('title', ''), 'description': item.get('description', '')}, 'questions': policy['questions']}
        fingerprint = hashlib.sha256(json.dumps([policy, body], sort_keys=True).encode()).hexdigest()
        return policy, body, fingerprint

    def cached(self, fingerprint):
        with closing(self.db()) as db:
            row = db.execute('SELECT payload FROM jev_classifications WHERE fingerprint=?', (fingerprint,)).fetchone()
        return json.loads(row[0]) if row else None

    def annotate(self, items):
        for item in items:
            _, _, fingerprint = self.request(item)
            item['role_classification'] = self.cached(fingerprint)

    def classify(self, item):
        policy, body, fingerprint = self.request(item)
        cached = self.cached(fingerprint)
        if cached:
            return {**cached, 'cached': True}
        key = os.environ.get('TYPESAFE_API_KEY', '').strip()
        if not key:
            return {'state': 'not_configured'}
        description = item.get('description', '').strip()
        if len(description) < 200:
            return {'state': 'needs_description'}
        if len(json.dumps(body)) > 60000:
            return {'state': 'too_long'}  # Never silently classify a truncated JD.
        started = time.monotonic()
        with closing(self.db()) as db, db:
            call_id = db.execute('''INSERT INTO jev_classification_calls
                (fingerprint,created_at,scan_id,opportunity_id,model,state) VALUES(?,?,?,?,?,?)''',
                (fingerprint, datetime.now(timezone.utc).isoformat(), scan_id.get(), item.get('id'), policy['model'], 'DISPATCHING')).lastrowid
        error = 'Jev request interrupted; billing is unknown.'
        try:
            response = self.post('https://api.typesafe.ai/v1/systemone', headers={'Authorization': f'Bearer {key}'}, json=body, timeout=25)
            response.raise_for_status()
            payload = response.json()
            # Preserve reported usage even when the classification is invalid.
            tokens = payload['usage']['input_tokens']
            outgoing = payload['usage'].get('output_tokens')
            if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                raise ValueError('Invalid usage')
            if outgoing is not None and (isinstance(outgoing, bool) or not isinstance(outgoing, int) or outgoing < 0):
                raise ValueError('Invalid usage')
            with closing(self.db()) as db, db:
                db.execute('UPDATE jev_classification_calls SET input_tokens=?,output_tokens=?,cost=? WHERE id=?',
                           (tokens, outgoing, tokens*policy['input_usd_per_million']/1e6, call_id))
            if payload.get('model') != policy['model']:
                raise ValueError('Unexpected model version')
            answers = {}
            for name, question in policy['questions'].items():
                answer = Choice.model_validate(payload['answers'][name])
                probs = answer.probabilities
                if (answer.type != 'choice' or answer.choice not in question['criteria'] or set(probs) != set(question['criteria'])
                        or any(not 0 <= p <= 1 for p in probs.values()) or abs(sum(probs.values()) - 1) > .02
                        or probs[answer.choice] < max(probs.values())):
                    raise ValueError('Invalid choice response')
                answers[name] = answer.model_dump()
            tokens = payload['usage']['input_tokens']
            if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                raise ValueError('Invalid usage')
            result = {'state': 'classified', 'answers': answers, 'model': payload['model'], 'policy_version': policy['version'],
                      'checked_at': datetime.now(timezone.utc).isoformat(), 'latency_ms': round((time.monotonic()-started)*1000),
                      'input_tokens': tokens, 'estimated_cost_usd': tokens * policy['input_usd_per_million'] / 1000000,
                      'labels': {name: policy['labels'][name][a['choice']] if a['confidence'] >= policy['display_confidence'] else 'Unclear'
                                 for name, a in answers.items()}}
            with closing(self.db()) as db, db:
                db.execute('INSERT OR REPLACE INTO jev_classifications VALUES (?,?)', (fingerprint, json.dumps(result)))
            error = None
            return result
        except httpx.HTTPStatusError as exc:
            error = f'Jev returned HTTP {exc.response.status_code}. No automatic retry was made.'
            return {'state': 'error', 'error': error}
        except httpx.RequestError:
            error = 'Jev connection failed. No automatic retry was made.'
            return {'state': 'error', 'error': error}
        except (ValueError, KeyError, TypeError):
            error = 'Jev returned an invalid classification. Local matching remains active.'
            return {'state': 'error', 'error': error}
        finally:
            with closing(self.db()) as db, db:
                db.execute('UPDATE jev_classification_calls SET state=?,error=?,latency_ms=? WHERE id=?',
                           ('RECEIVED' if error is None else 'FAILED_OR_UNKNOWN', error, round((time.monotonic()-started)*1000), call_id))

    def run(self, items, stop):
        counts = {'classified': 0, 'cached': 0, 'skipped': 0, 'state': 'complete'}
        if not self.status()['configured']:
            return {**counts, 'state': 'not_configured'}
        for item in items:
            if stop.is_set():
                return {**counts, 'state': 'interrupted'}
            if item.get('location_match', {}).get('status') == 'outside':
                counts['skipped'] += 1
                continue
            result = self.classify(item)
            if result['state'] == 'error':
                return {**counts, **result}  # Circuit break for this scan, not one failure per listing.
            counts['cached' if result.get('cached') else 'classified' if result['state'] == 'classified' else 'skipped'] += 1
        return counts
