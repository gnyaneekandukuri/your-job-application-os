"""Small OpenAI HTTPS adapter. Never logs credentials, source text or error bodies."""
import os

import httpx

from .retrieval import RetrievalError


class OpenAIProvider:
    @property
    def configured(self):
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())

    def request(self, endpoint: str, payload: dict) -> dict:
        if endpoint not in ("responses", "embeddings"):
            raise RetrievalError("Unsupported model endpoint.")
        try:
            # No redirects, environment proxies, automatic retries or tools.
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), trust_env=False,
                              follow_redirects=False) as client:
                response = client.post("https://api.openai.com/v1/" + endpoint,
                                       headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                                       json=payload)
            if not response.is_success:
                message = {401: "API key rejected. Check your local replacement key.",
                           403: "API access denied. Check project permissions and model access.",
                           404: "Requested model or endpoint is unavailable to this project.",
                           429: "API quota or rate limit reached. Check billing and limits."}.get(
                               response.status_code, "OpenAI request failed. Check model access and request compatibility.")
                raise RetrievalError(message + f" (HTTP {response.status_code}). No automatic retry was made.", 502)
            return response.json()
        except (httpx.HTTPError, ValueError, KeyError):
            raise RetrievalError("OpenAI connection or response failed. Billing may be unknown; no automatic retry was made.", 502) from None
