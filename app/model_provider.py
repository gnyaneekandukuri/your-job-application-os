"""Small OpenAI HTTPS adapter. Never logs credentials, source text or error bodies."""
import os

import httpx

from .retrieval import RetrievalError


def failure_message(response):
    """Translate only recognized codes; never expose the provider's raw body."""
    message = {401: "API key rejected. Check your local replacement key.",
               403: "API access denied. Check project permissions and model access.",
               404: "Requested model or endpoint is unavailable to this project."}.get(
                   response.status_code, "OpenAI request failed. Check model access and request compatibility.")
    if response.status_code == 400:
        try:
            body = response.json()
            error = body.get('error', {}) if isinstance(body, dict) else {}
            code = error.get('code') if isinstance(error, dict) else None
        except ValueError:
            code = None
        if code == 'invalid_json_schema':
            message = 'The app sent an invalid response format. This needs an application fix; your saved draft is preserved.'
        elif code == 'context_length_exceeded':
            message = 'The request exceeds the model context window. Your saved draft is preserved.'
    if response.status_code == 429:
        try:
            body = response.json()
            error = body.get('error', {}) if isinstance(body, dict) else {}
            if not isinstance(error, dict): error = {}
        except ValueError:
            error = {}
        messages = {
            'credit_balance_exhausted': 'OpenAI API credits are exhausted. Add credits in API billing, then resume this analysis.',
            'organization_spend_limit_exceeded': 'OpenAI organization spending limit reached. Review the organization limit, then resume this analysis.',
            'project_spend_limit_exceeded': 'OpenAI project spending limit reached. Review this API project’s limit, then resume this analysis.',
            'organization_usage_limit_exceeded': 'OpenAI organization usage allowance reached. Review your approved API usage limit before resuming.',
            'insufficient_quota': 'OpenAI API quota is unavailable. Check API credit balance and organization/project limits, then resume. This is not a temporary request-rate limit.',
            'rate_limit_exceeded': 'OpenAI temporary request/token rate limit reached. Wait before resuming; this error does not establish a billing problem.',
            'slow_down': 'OpenAI asked us to slow down. Wait before resuming; this error does not establish a billing problem.',
        }
        code = error.get('code')
        kind = error.get('type')
        message = messages.get(code) if isinstance(code, str) else None
        if message is None:
            message = messages.get(kind) if isinstance(kind, str) else None
        if message is None and kind == 'rate_limit_error':
            message = messages['rate_limit_exceeded']
        if message is None:
            message = 'OpenAI returned HTTP 429 without a recognized cause. Billing versus temporary rate limiting could not be determined.'
        delay = response.headers.get('retry-after', '')
        if delay.isdecimal() and len(delay) <= 6:
            message += f' Retry no sooner than {delay} seconds.'
    return message + f' (HTTP {response.status_code}). No automatic retry was made.'


class OpenAIProvider:
    def __init__(self, allow_generation=True, allow_embeddings=True, request_timeout=60):
        self.request_timeout = request_timeout
        self.allow_generation = allow_generation
        self.allow_embeddings = allow_embeddings

    @property
    def configured(self):
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())

    def request(self, endpoint: str, payload: dict) -> dict:
        if endpoint == 'embeddings' and not self.allow_embeddings:
            raise RetrievalError('OpenAI embeddings are disabled in the Jev workflow. Prepare and search context locally; no OpenAI request was sent.', 409)
        if endpoint == 'responses' and not self.allow_generation:
            raise RetrievalError('OpenAI generation is disabled for assessment and review. Use the Jev assessment or edit your context directly. No OpenAI request was sent.', 409)
        if endpoint not in ("responses", "embeddings"):
            raise RetrievalError("Unsupported model endpoint.")
        try:
            # No redirects, environment proxies, automatic retries or tools.
            with httpx.Client(timeout=httpx.Timeout(self.request_timeout, connect=10), trust_env=False,
                              follow_redirects=False) as client:
                response = client.post("https://api.openai.com/v1/" + endpoint,
                                       headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                                       json=payload)
            if not response.is_success:
                raise RetrievalError(failure_message(response), 502)
            return response.json()
        except (httpx.HTTPError, ValueError, KeyError):
            raise RetrievalError("OpenAI connection or response failed. Billing may be unknown; no automatic retry was made.", 502) from None
