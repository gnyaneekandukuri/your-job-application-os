# Jev assessment (October 2, 2026)

The active app uses TypeSafe Jev for typed classification. It does not ask Jev to write prose or estimate interview probability. Existing LangGraph review checkpoints, Pydantic schemas, SQLite history, deterministic numeric anchors and personal preference files remain in use.

## Flow

1. Board integrations fetch descriptions. Local preferences and weighted keywords prioritize listings; discovery Jev classifications remain advisory.
2. Jev classifies original JD blocks as responsibilities, requirements, preferences, unclear text, context, or unsafe instructions. Original text preserves OR alternatives and negation. Uncertain categories are excluded from numeric weighting pending review.
3. Local BM25 selects up to twelve original excerpts per requirement, with several whole-JD matches for breadth. No embeddings are requested. Jev checks which excerpts support the requirement; up to three are retained for dimension assessment.
4. Jev selects defined dimension anchors and alignment/conflict labels. Python validates identities, exact source quotes, finite probabilities, and allowed choices, then computes the existing weighted score. Explanations are templates, not generated narratives.
5. Jev identifies explicit preference evidence and core requirements; deterministic rules produce pursuit guidance. Silence about sponsorship remains unknown. A score is documented evidence coverage, not hiring probability.
6. Tentative dimension choices retain their anchor but make the score provisional. They prevent automatic Attack Hard. Human confirmation records review without increasing numeric credit. New accounts are saved verbatim only after approval; merely requesting a higher score provides no evidence.

## Boundaries and cost

The app's OpenAI provider rejects both generation and embedding calls before network access. Library preparation is local. Library search, review routing and explicit preference proposals use Jev. New experience proposals preserve the user's words; mixed or uncertain destinations require clarification. Preference learning uses allowlisted fields and explicit numbers or quoted list entries, with approval before changes. It does not synthesize new policy text.

Successful Jev responses are cached by model, policy, question definitions and full input content. Changed evidence or rules changes the key. Each dispatched attempt has a durable token/cost ledger; malformed answers with reported usage are still counted. Unknown outcomes remain unknown, not zero-cost successes. No automatic retry or OpenAI fallback occurs. API costs shows historical OpenAI spending alongside Jev discovery and assessment calls.

Evidence is capped at 48,000 serialized bytes and each request at 180,000 bytes. Oversized requests stop explicitly. Old reports remain available as previous results; they are not silently relabeled as Jev reports or all regenerated. Automatic assessment is opt-in for new installations and retains existing pause settings on upgrade.

## Quality limits

This is a cost-focused implementation, not a demonstrated replacement for generative reasoning on every requirement. Lexical retrieval can miss semantically relevant stories before Jev sees them. Jev confidence is not calibrated against this user's hiring outcomes. The 0.8 review threshold is a configurable product policy, not a universal standard. Do not describe these scores as probability of interview or success.

Tests cover caching, durable errors, exact quotes, OR/negation preservation, zero OpenAI calls through the full assessment flow, fresh-install behavior, and human review of provisional results. Real-data checks and human-labelled examples are still needed for retrieval recall and score calibration. A completed live run proves connectivity and workflow, not judgment accuracy.

## Primary references

- [TypeSafe API](https://docs.typesafe.ai/api): typed choice responses and usage.
- [Confidence](https://docs.typesafe.ai/confidence): model confidence is separate from the selected class.
- [Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring): typed judgments composed into application scores.
- [RAG passage classification](https://docs.typesafe.ai/cookbooks/classifying_rag_passages): relevance classification after retrieval.
