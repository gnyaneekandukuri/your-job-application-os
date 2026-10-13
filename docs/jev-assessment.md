# Jev assessment (October 2, 2026)

The active app uses API embeddings for semantic retrieval and TypeSafe Jev for typed classification. It does not ask Jev to write prose or estimate interview probability. Existing LangGraph review checkpoints, Pydantic schemas, SQLite history, deterministic numeric anchors and personal preference files remain in use.

## Flow

1. Board integrations fetch descriptions. Local preferences, source-backed keywords and candidate-aware Jev screening prioritize listings. Target preferences are separate from documented experience. These calls contain compact source excerpts but no embeddings. Successful current High priority classifications are eligible for automatic assessment; other jobs require a manual assessment request.
2. Jev classifies original JD blocks as responsibilities, requirements, preferences, unclear text, context, or unsafe instructions. Original text preserves OR alternatives and negation. Uncertain categories are excluded from numeric weighting pending review.
3. Context preparation caches OpenAI embeddings of original excerpts. Each requirement gets a cached query embedding. Local cosine similarity plus BM25/reciprocal-rank fusion selects up to twelve excerpts, reserving six slots for semantic matches. No lexical-only fallback occurs if source vectors are missing. Jev checks which excerpts support the requirement; up to three are retained for dimension assessment.
4. Jev selects defined dimension anchors and alignment/conflict labels. Python validates identities, exact source quotes, finite probabilities, and allowed choices, then computes the existing weighted score. Explanations are templates, not generated narratives.
5. Jev identifies explicit preference evidence and core requirements; deterministic rules produce pursuit guidance. Silence about sponsorship remains unknown. A score is documented evidence coverage, not hiring probability.
6. Tentative dimension choices retain their anchor but make the score provisional. They prevent automatic Attack Hard. Human confirmation records review without increasing numeric credit. New accounts are saved verbatim only after approval; merely requesting a higher score provides no evidence.

## Boundaries and cost

The app's OpenAI provider permits embeddings and rejects generation before network access. Library preparation sends only new or changed original text for embedding; it does not generate summaries. Library search, review routing and explicit preference proposals use Jev. New experience proposals preserve the user's words; mixed or uncertain destinations require clarification. Preference learning uses allowlisted fields and explicit numbers or quoted list entries, with approval before changes. It does not synthesize new policy text.

Successful Jev responses are cached by model, policy, question definitions and full input content. Changed evidence or rules changes the key. Each dispatched attempt has a durable token/cost ledger; malformed answers with reported usage are still counted. Unknown outcomes remain unknown, not zero-cost successes. No automatic retry or OpenAI fallback occurs. API costs shows historical OpenAI spending alongside Jev discovery and assessment calls.

Evidence is capped at 48,000 serialized bytes and each request at 180,000 bytes. Oversized requests stop explicitly. Old reports remain available as previous results; they are not silently relabeled as Jev reports or all regenerated. Automatic assessment is opt-in for new installations and retains existing pause settings on upgrade.

## Quality limits

This is a cost-focused implementation, not a demonstrated replacement for generative reasoning on every requirement. Semantic retrieval can still miss relevant stories or surface passages that are related but insufficient. Retrieval similarity is not capability strength. Jev confidence is not calibrated against this user's hiring outcomes. The 0.8 review threshold is a configurable product policy, not a universal standard. Do not describe these scores as probability of interview or success.

Tests cover caching, durable errors, exact quotes, OR/negation preservation, embedding-only OpenAI calls through the full assessment flow, fresh-install behavior, and human review of provisional results. Real-data checks and human-labelled examples are still needed for retrieval recall and score calibration. A completed live run proves connectivity and workflow, not judgment accuracy.

## Primary references

- [TypeSafe API](https://docs.typesafe.ai/api): typed choice responses and usage.
- [Confidence](https://docs.typesafe.ai/confidence): model confidence is separate from the selected class.
- [Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring): typed judgments composed into application scores.
- [RAG passage classification](https://docs.typesafe.ai/cookbooks/classifying_rag_passages): relevance classification after retrieval.

## Updates and rollout

Existing text-only indexes need preparation once for the semantic version. Unchanged excerpt embeddings are shared by exact text, embedding model and dimensions; moved line numbers and additional jobs do not require re-embedding the same text. Removed documents/chunks leave active retrieval immediately; history stays local. Changing the embedding model requires a compatible index rebuild.

The first-pass profile uses the existing versioned concept vocabulary, source-linked snippets and separate target preferences. It is not a complete inferred inventory of every candidate skill. Profile/target/JD changes invalidate screening; until the next scan evaluates it, the role is Worth checking rather than silently retaining a stale High priority label. Refreshing the page makes no provider calls. A failed screening is retained for that input; provider errors never become Skip.

Automated tests include incremental preparation, cross-job query caching, differently worded evidence among lexical distractors, first-pass exclusion of embeddings, and automatic High-priority handoff. Live OpenAI verification must succeed before resuming the personal automatic queue.

Initial screening combines the existing weighted keyword High signal with Jev probability mass on strong/plausible relevance (at least 0.9). A decisive strong classification can also promote a role without exact keyword overlap. A decisive mismatch is required for Skip. Thresholds are in `criteria/discovery-screening.json`; these routing signals are not evidence scores.

The fast profile includes uploaded experience/resume documents and the configured current master resume. Historical non-authoritative resume versions and coaching remain excluded from experience screening. Current-master claims are initial signals, not automatic evidence credit.

Live TypeSafe sometimes returned a chosen label within 0.02 probability of a different leading label. Such contradictory near-ties retain the raw provider reply but receive zero classifier confidence and an explicit review flag; they cannot independently establish a trusted decision. Larger contradictions are rejected. This is an integration safeguard, not a claim that the provider output was correct.

Deep analysis searches firsthand stories and also the current master resume. Stories supply how the work was done; a supporting resume line can join the selected evidence to supply numbers, scope or results that stories omit. Resume lines carry a caveat, and numbers covered by fact-conflict rules stay unresolved.

Initial screening sends up to five original passages: titles, headings and short fragments are excluded, stories are preferred over resume lines (at most two resume passages and two per story), passages are matched against individual JD sections, and the candidate's locally matched skills are included. An optional private `knowledge/screening-anchors.json` lists lead stories that receive a small preference. Titles above Senior PM are labeled Low locally without a Jev call.
