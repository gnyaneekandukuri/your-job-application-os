# Assessment retrieval decision

**Superseded later on October 2, 2026:** the current design now uses Jev assessment and API embeddings for local semantic retrieval, with generative OpenAI reserved for future tailoring. [The current design](jev-assessment.md) replaces the OpenAI steps described below. This document preserves the earlier comparison and evaluation limitations; its hybrid embedding workflow is no longer the live app path.

Decision (October 2, 2026): retain a controlled retrieve-then-assess workflow. Do not introduce an autonomous tool loop or remove semantic requirement interpretation without measured quality evidence. No architecture is universally best.

## Implemented flow and cost boundaries

1. Fetch the listing/description through board integrations. No OpenAI interpretation is needed to receive JSON or HTML.
2. Screen with local preferences/keyword matches and advisory Jev classification (separate TypeSafe API cost). This is prioritization, not evidence scoring.
3. For deep assessment, interpret the JD into quoted requirements with a cached OpenAI structured-output call. Required/preferred/ambiguous, OR alternatives and exceptions matter. This is different from fetching the description. Identical descriptions may now reuse successful validated interpretation across job records; model/prompt/version/content changes invalidate the shared cache. Failed outputs never become shared interpretations.
4. Retrieve original evidence locally with BM25 and semantic similarity combined using reciprocal rank fusion. Stored story embeddings are reused. New requirement-query embeddings cost a small OpenAI request and are cached. `retrieve_evidence` is the backend's callable search boundary; it has no network access and is not an LLM-invoked tool.
5. Assess four requirements per OpenAI call against selected passages and the authority rules. Top four passages per requirement are merged under a 48,000-byte evidence budget. Never send the complete library as fallback. If the strongest candidate for every requirement cannot fit, stop explicitly rather than silently starving a requirement. Local library size no longer inherits the old whole-prompt limit.
6. Validate structured output and original-source quotations, compute scores in code, then use a separate cached role/preference interpretation and deterministic pursuit rules. Existing LangGraph checkpoints, review decisions and local cost tracking remain in place.

Retrieval traces record the candidate count, requirement-to-candidate mapping, selected IDs, source revisions and bytes. Ranking is not evidence strength. No paid model reranker or model-directed follow-up search was added. Other legacy library-search/individual-review paths are separate and are not claimed to have been redesigned here.

## Options considered

- Sending the complete corpus repeatedly has high input cost; it remains only a historical comparison, never a runtime fallback.
- Searching directly with a whole JD and merging extraction/assessment could remove a model call, but changes requirement coverage, review checkpoints and mandatory-versus-preferred interpretation. It needs an end-to-end evaluation, not just a cheaper call count.
- Structured requirements plus hybrid retrieval preserves explicit requirement coverage and bounded evidence costs. This is the current choice, not a claim of measured superiority on every dataset.
- Agentic search lets the model reformulate queries and retrieve again. It adds variable calls, latency and failure modes. Introduce it only if an evaluation shows a specific recall problem it improves, with explicit search/iteration bounds.

## Evaluation and limitations

Run `.venv/Scripts/python.exe scripts/evaluate_retrieval.py` from the product root. Default mode never sends provider requests: it compares local lexical baselines and runs hybrid variants only for cached query vectors. `--embed-missing` explicitly permits query embeddings only (at most 250 KB per invocation), never assessment generation. The detailed report stays under the active private workspace. The ordinary cost ledger records attempted embedding calls.

The replay uses unchanged, exact-source citations from saved completed reports as a regression reference. They are model-generated, not human-labelled ground truth. It excludes changed JDs/sources. Cases without citations are not evaluated, so this does not test correct abstention. It reports citation recall and bytes, not score correctness, qualification probability, or measured end-to-end savings. Whole-JD and per-requirement top-four variants are measured per query; multi-requirement packing is tested separately in the unit suite.

October 2 local check: 80 cited requirements. Mean recovery of previously cited passages was 28.75% for whole-JD lexical top-four, 18.54% for original-quote lexical top-four, and 16.25% for interpretation-plus-quote lexical top-four. These low values do not validate keyword-only retrieval; they also do not prove whole-JD assessment is better. All 80 semantic/hybrid cases lacked query embeddings. The embedding attempt returned HTTP 429 / credits exhausted; no retry occurred. No new assessment quality comparison was run. Automatic paid assessment remains paused in the active personal workspace.

Next quality gate: curate human-reviewed cases covering direct evidence, transferable experience, missing evidence, negation/OR and company-description distractors. Compare requirement coverage, retrieved evidence recall, unsupported claims, score/recommendation agreement and actual cost. Test whole-JD/section-based retrieval against per-requirement retrieval before deleting the extraction call or adding an agent loop.

## Primary documentation

- [LangChain retrieval architectures](https://docs.langchain.com/oss/python/deepagents/retrieval): distinguishes predictable retrieval-before-generation from agent-selected retrieval; neither is universally best.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): start with the simplest sufficient workflow; added autonomy trades cost and latency for potential capability.
- [OpenAI retrieval](https://developers.openai.com/api/docs/guides/retrieval): supports semantic and text ranking combined with reciprocal rank fusion. We use a local implementation, not hosted vector stores.
- [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices): use task-specific datasets, compare alternatives, include edge cases and human judgement. The example metric thresholds in the guide are not universal release criteria.
