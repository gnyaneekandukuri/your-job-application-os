# Discovery sources and assessment handoff

Reviewed September 30, 2026. Collection is local and uses no language model or Firecrawl.

- **YC:** now uses an app-owned, authenticated Microsoft Edge session for the Work at a Startup Product / Fulltime directory. Connect YC in Discovery runs; login is required. The old public category adapter remains in source for reference but is no longer selected by the live default collector. Browser session state is private and never copied from Codex.
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



## Connection recovery and coverage (October 1, 2026)

Free board requests retry transient network/time-out errors and HTTP 502/503/504 after 10, 30, and 60 seconds, for at most four attempts per request. Shutdown interrupts retry waits. HTTP 401/403/429, response-format errors, and responses with Retry-After are not immediately retried. These retries do not apply to paid analysis. A failed source can be tried at the next scheduled scan or through Check now.

Run status is failed if no board could be collected, incomplete if a source failed or any collector reports partial coverage, and complete only when all collectors report checked coverage. Previously saved complete statuses are interpreted with the same rules when displayed; historical records are not rewritten. Jobs from successful sources and all older listings remain saved. Incomplete coverage is visible on New opportunities and Discovery runs.

Diagnostics retain the exception category, HTTP status, endpoint host/path, attempt count, and exception cause types. Detail-page failures also carry diagnostics. Response bodies, cookies, query strings, and raw error messages are not recorded. Exact causes such as bot protection cannot be inferred from HTTP 403 alone.

Official feed review: https://docs.considerapi.com/ documents authenticated board jobs with X-API-Key. A key must be issued through Consider support/customer success, and access to our target boards has not been established. No supported public YC or Built In collection API was verified. Their website collectors remain explicitly partial; no paid third-party feed or guessed API has been substituted.


## Authenticated YC directory

The browser profile lives in the active private directory under `yc-browser/profile`. Login and scans are exclusive operations managed by a subprocess, so scheduler threads do not share Playwright contexts. Connect YC opens a visible Edge window; checks and scheduled collection use a headless instance of the same profile. All operations verify the signed-in directory. A missing/expired session is reported as `login_required`, independently of HTTP access denial. Other boards continue and existing listings remain saved.

Directory scrolling is bounded to 50 steps, stops after three unchanged checks, and reports loaded vs reported startup counts. At most 120 detail pages are attempted within a ten-minute scan budget. Only matching product titles are collected; filtering by the user's country and priority continues in the existing pipeline. Descriptions require a matching job/company heading and an About the role section. Partial or missing descriptions are reported. No passwords or cookies appear in diagnostics; the profile is Git-ignored. Live authenticated verification is required before claiming this connection works on a given installation.


YC verification fixes: an initial zero count is treated as a loading placeholder, not an empty result; empty results must settle before collection ends. Check YC now runs only YC, with other source results labeled not checked for that run. Discovery uses SQLite WAL, and dashboard assembly releases the listing read before building expensive assessment summaries, so dashboard polling does not hold up scan writes.


YC description reads wait for rendered headings and validate the job/company against all H1 headings, since employers may include their own H1 headings inside descriptions. Descriptions are reused for up to four hours only when URL, title, and company match; reuse preserves the original check timestamp rather than extending it. This lets repeated scans spend their 120-request budget on missing descriptions. Legacy entries without a check timestamp use their last-seen time once during migration. Existing descriptions can therefore lag employer edits within this cache window; newly discovered roles are fetched immediately.
