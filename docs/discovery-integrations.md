# Discovery sources and assessment handoff

Reviewed September 30, 2026. Collection is local and uses no language model or Firecrawl.

- **YC:** reads embedded listing JSON from the public Product Manager category page and JobPosting JSON-LD from individual YC listings. Up to 60 details. This is a website adapter, not a documented developer API or complete YC feed.
- **Built In:** reads public product-category listing pages and JobPosting JSON-LD from individual listings. Up to five category pages and 40 details. Coverage remains partial; calendar dates do not prove a precise 24-hour publication window.
- **a16z, Sequoia, Lightspeed:** use the boards' website search endpoint `/api-boards/search-jobs`, with an anonymous session token, product-title filter, and board-provided past-day filter. Cursor pagination is bounded to five pages. The response may contain only metadata. Board timestamps are not verified original employer publication dates.
- **Wellfound, Khosla, Index:** previously blocked direct access; not enabled. Welcome to the Jungle remains unverified.

Consider separately documents an authenticated Boards API at https://docs.considerapi.com/ with pagination, date filtering, and optional descriptions. That API requires a provisioned API key. We have not established access to the relevant boards through it and do not present the website adapter as that supported API. No credential is embedded in the product.

## Processing

Each scan collects and deduplicates normalized records in SQLite, retaining per-board sightings, content revisions, source timestamps, and first/last detection times. It does not archive every original response. Failed individual sources remain visible in the run summary and do not erase previously saved jobs.

For missing descriptions in the current scan, the app attempts the exact listing URL through the existing bounded, SSRF-protected extractor. Known Ashby/Greenhouse URLs use their structured feeds where supported; other pages require unambiguous JobPosting data. Automatic acceptance requires matching normalized company and title and at least 200 description characters. Failed, ambiguous, or mismatched extraction asks the user to open the listing and paste the JD. Failed automatic fetches wait at least 24 hours before another automatic attempt; explicit fetch remains available.

After descriptions are saved, cached local keyword matching recalculates priority. High priority, country-matched, full-description jobs seen in the scan can enter paid analysis when enabled. Posting age does not block assessment. Historical listings absent from this scan are not silently queued. Existing complete, failed, interrupted, stale, or already-dispatched analyses are not automatically charged again. Explicit manual resume/reassessment remains available.

The scan records collection, description retrieval, and assessment phases. It finishes after the last running assessment worker finishes. The opportunity row shows the score and separate pursuit recommendation; partial results and errors remain visible. User-pasted descriptions are saved and reprioritized immediately; use the row's analysis action or the next scan to assess them.

“Source reports <24h” and “Discovered in last 24h” are independent filters. Discovering an old job today does not make it newly posted. Neither a still-listed job nor an aggregator timestamp guarantees that the employer is still hiring.

## Deferred

Gmail and phone alerts are a later milestone: notify after assessment with the role, score, pursuit recommendation, and listing link, with deduplication and user-selected delivery preferences. No notification service is configured or messages sent by this feature.

## Results screen and provider limits

Deep analysis lists saved analyses from both discovery and manually entered roles. Completed score/recommendation pairs appear first; running and failed/interrupted/stale work have separate views. Completed evidence analyses leave New opportunities, including those whose pursuit recommendation still needs attention. Records remain saved.

An HTTP 429 from either evidence analysis or pursuit pauses the automatic paid queue before dispatching another role. Free scheduled collection remains enabled. Failed roles are not automatically retried after re-enabling the queue. Historical error messages alone cannot distinguish exhausted quota from a temporary rate limit; no account billing state is inferred.

