# Local API costs

The API costs page reads local ledgers; viewing it makes no provider calls and needs no LangSmith subscription. Filter by date and provider. Unknown usage stays unknown, not zero. It is an estimate of this app's recorded calls, not a provider invoice or other apps' spending.

The active assessment path is Jev-only. Local context preparation and BM25 retrieval make no model requests. Jev classifies original requirements, evidence relevance, dimensions and preference evidence. Library search and review routing also use Jev. The app blocks OpenAI generation and embeddings before network access. See [the current design](jev-assessment.md).

New Jev assessment calls record job, stage, input/output tokens, latency, state and estimated cost. Current configured input rate is $0.042 per million tokens, with no output-token charge; it is versioned in `criteria/jev-assessment.json`, not fetched dynamically. Successful discovery classifications also contribute their cached usage. Failed requests without reported usage and standalone scripts outside the ledger cannot be fully reconciled here.

Historical OpenAI costs remain visible. GPT-6 Sol estimates use reported input, output, cache-read and cache-write counts at $2/$10/$0.20/$2.50 per million respectively. Unknown model overrides carry an unverified estimate label. Existing historical estimates are retained. No new spending cap has been introduced.

Successful Jev results are cached against their complete input and policy. Changes to sources or questions invalidate that cache. Errors are recorded without automatic retries; the user must explicitly resume. Selected evidence is bounded; the full library is never a fallback. Previous completed reports remain available without automatic rescoring. TypeSafe and OpenAI billing accounts are separate.
