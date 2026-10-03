"""TypeSafe assessment calls: validated choices, durable attempts, no OpenAI fallback."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx

from .retrieval import RetrievalError, digest, now

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'criteria/jev-assessment.json').read_text())
MODEL = POLICY['model']
VERSION = POLICY['version'] + ':' + digest(json.dumps(POLICY, sort_keys=True))
FLOOR = POLICY['confidence_floor']


class JevValidationError(ValueError):
    """Only fixed, non-sensitive validation messages belong in this exception."""


def choice(instructions, criteria):
    return {'type': 'choice', 'instructions': instructions, 'criteria': criteria}


def confident(answer):
    return answer['confidence'] >= FLOOR


def validate_answers(response, questions):
    if response.get('model') != MODEL or set(response.get('answers', {})) != set(questions):
        raise JevValidationError('Unexpected model or answer identities')
    validated = {}
    for name, question in questions.items():
        a = response['answers'][name]
        probabilities = a['probabilities']
        if a['type'] != 'choice' or set(probabilities) != set(question['criteria']):
            raise JevValidationError('Unexpected choice domain')
        numbers = [a['confidence'], *probabilities.values()]
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= 1 for n in numbers):
            raise JevValidationError('Invalid probabilities')
        if abs(sum(probabilities.values()) - 1) > .020001:
            raise JevValidationError('Probabilities do not sum to one within rounding tolerance')
        if a['choice'] not in probabilities:
            raise JevValidationError('Invalid selected choice')
        discrepancy = max(probabilities.values()) - probabilities[a['choice']]
        if discrepancy > .020001:
            raise JevValidationError('Selected choice materially contradicts its probabilities')
        # The live provider occasionally selects one of two near-tied labels
        # contrary to its distribution. Preserve its label and raw cached reply,
        # but give it zero trust and make the inconsistency reviewable.
        validated[name] = {**a, **({'confidence': 0., 'provider_inconsistent': True} if discrepancy > 0 else {})}
    return validated


class JevClient:
    def __init__(self, engine, post=None):
        self.engine, self.post = engine, post
        # A caller creates a client for an explicit run/retry. A failed call is
        # never retried inside that run; unknown outcomes remain in the ledger.
        self.attempt_id = str(uuid4())
        with closing(engine.connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS jev_assessment_calls(
                id INTEGER PRIMARY KEY, created_at TEXT, job_id TEXT, stage TEXT,
                model TEXT, state TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cost REAL, latency_ms INTEGER, error TEXT)''')

    def evaluate(self, job_id, stage, state, questions):
        if not questions:
            return {}
        if any(not 2 <= len(q.get('criteria', {})) <= 255 for q in questions.values()):
            raise RetrievalError('Jev choices must contain 2–255 options. Nothing was sent.', 409)
        body = {'model': MODEL, 'state': state, 'questions': questions}
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode()) > POLICY['max_request_bytes']:
            raise RetrievalError('Jev request is too large. Split the source text; nothing was sent.', 409)
        key = 'jev-assessment:' + digest(VERSION + encoded)
        with self.engine.lock:
            saved = self.engine.get(key)
            if saved and 'response' in saved:
                return validate_answers(saved['response'], questions)
            if saved and saved.get('attempt_id') == self.attempt_id:
                raise RetrievalError('This Jev request already failed or has an unknown outcome. Resume explicitly to retry.', 409)
            secret = os.environ.get('TYPESAFE_API_KEY', '').strip()
            if not secret and self.post is None:
                raise RetrievalError('Connect your TypeSafe key to run Jev assessment. No OpenAI fallback is used.', 503)
            self.engine.put(key, {'attempt_id': self.attempt_id, 'state': 'DISPATCHING'})
            with closing(self.engine.connect()) as db, db:
                call_id = db.execute('INSERT INTO jev_assessment_calls(created_at,job_id,stage,model,state) VALUES(?,?,?,?,?)',
                                     (now(), job_id, stage, MODEL, 'DISPATCHING')).lastrowid
            started = time.monotonic()
            try:
                if self.post:
                    response = self.post(body)
                else:
                    with httpx.Client(trust_env=False, follow_redirects=False, timeout=45) as client:
                        response = client.post('https://api.typesafe.ai/v1/systemone',
                                               headers={'Authorization': 'Bearer '+secret}, json=body)
                    if not response.is_success:
                        raise RetrievalError(f'TypeSafe Jev returned HTTP {response.status_code}. Check TypeSafe access or balance; no automatic retry or OpenAI fallback was made.', 502)
                    response = response.json()
                usage = response['usage']
                incoming, outgoing = usage['input_tokens'], usage.get('output_tokens', 0)
                if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (incoming, outgoing)):
                    raise ValueError('Invalid usage')
                # Record reported usage even if the answers fail validation.
                with closing(self.engine.connect()) as db, db:
                    db.execute('UPDATE jev_assessment_calls SET input_tokens=?,output_tokens=?,cost=? WHERE id=?',
                               (incoming, outgoing, incoming*POLICY['input_usd_per_million']/1e6, call_id))
                answers = validate_answers(response, questions)
                self.engine.put(key, {'response': response})
                with closing(self.engine.connect()) as db, db:
                    db.execute("UPDATE jev_assessment_calls SET state='RECEIVED',latency_ms=? WHERE id=?",
                               (round((time.monotonic()-started)*1000), call_id))
                return answers
            except Exception as exc:
                message = str(exc) if isinstance(exc, RetrievalError) else 'Jev connection or response validation failed. Billing may be unknown; no automatic retry or OpenAI fallback was made.'
                if isinstance(exc, JevValidationError):
                    message = 'Jev response failed validation: '+str(exc)+'. Reported usage is recorded; no automatic retry was made.'
                    # Typed labels only, private diagnostic cache; never use as a result.
                    self.engine.put('jev-invalid-response:'+str(call_id), response)
                self.engine.put(key, {'attempt_id': self.attempt_id, 'state': 'FAILED_OR_UNKNOWN'})
                with closing(self.engine.connect()) as db, db:
                    db.execute("UPDATE jev_assessment_calls SET state='FAILED_OR_UNKNOWN',error=?,latency_ms=? WHERE id=?",
                               (message, round((time.monotonic()-started)*1000), call_id))
                raise RetrievalError(message, 502) from None
