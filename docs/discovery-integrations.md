# Discovery sources and assessment handoff

## Recovery and backlog operations

This section supersedes the older retry/window/processing notes below. Free HTTP reads retry timeouts, connection failures, 5xx and 429 with exponential delays of 10/20/40 seconds plus up to 25% jitter, at most four attempts. Retry-After (seconds or HTTP date) is a minimum wait. Delays above 120 seconds defer the source instead of sending an early retry. Browser operations retry recognized transient failures at most three times; request-level retry budgets are never multiplied. Paid calls are never automatically retried. Login, 401/403/999 and access challenges do not receive immediate retries. Persisted rate-limit cooldowns last at least 15 minutes; login/access blocks last at least an hour. Longer Retry-After values take precedence, including on 5xx responses.

Each source records start/end timestamps, attempts (maximum attempts for an individual request or browser operation), request count, safe error type/message, cooldown and lookback. LinkedIn pagination retains HTTP/network diagnostics along with partial results. Run details expose these fields in the API and board detail panel. No response bodies, cookies, query strings or credentials are logged. Historical records lacking diagnostics cannot be reconstructed.

The last successful source fetch is derived durably from scan records, using its start time conservatively. Errors and failed LinkedIn pagination do not advance it; Built In listing failures also preserve the previous success because those detail pages supply the listing records. Catch-up includes an hour of overlap and chooses 1/7/30-day windows, capped at 30 days. LinkedIn verifies the matching public date filter and retains older jobs inside wider searches. Built In sends daysSinceUpdated; venture boards retain their normal seven-day minimum and widen postedSince as needed. Lenny uses its verified All radio for gaps beyond a day, with bounded pagination, because that control is established in the existing connector; it has no temporal cap in All mode. YC reads its full directory; HN reads the current monthly thread. Pagination bounds still limit recovery: these collectors cannot guarantee every missed opening.

A nonblocking scan lock covers direct calls, scheduler requests and backlog repair. An immediate SQLite transaction rejects fresh unfinished runs and marks records older than two hours abandoned before claiming a new run. Active threads retain the lock until they unwind; the app never starts a second collector while an old one still runs. A cooperative wall-clock deadline checks cancellation between requests, jobs and phases, including after a browser returns from sleep. It retains saved listings, stops further work, and records abandonment. Network/browser subprocess bounds still apply; the deadline cannot cancel a paid request already dispatched. Startup still marks interrupted process work explicitly.

Description retrieval now selects saved, unimported, unpassed jobs rather than only current sightings: active/kept first, then newest discovery first. It uses the existing bounded subprocess around the same extract_job logic as the manual fetch endpoint, preserving SSRF, title/company identity and minimum-description checks. At most 50 descriptions are attempted per run, with five seconds between completions and the next attempt at the same original host. Failed attempts retain the 24-hour cooldown. JavaScript/login-only pages and mismatched metadata still require manual paste.

Initial semantic screening similarly selects up to 200 uncached eligible saved jobs per run, active/kept first and newest first, independently of current sightings. Existing provider errors remain explicit-retry work. Cached current/previous-policy results consume no new paid attempt. Successful backlog screens can enter the ordinary opt-in automatic assessment queue during normal scans. Explicit repair batches do not collect boards, run advisory classification, or queue deep assessment.

Preview a current workspace without opening the application or making any requests:

```powershell
.venv\Scripts\python.exe -m scripts.process_discovery_backlog --data-dir PATH_TO_PRIVATE_DATA --dry-run
```

Dry-run is the default. It opens only the existing job-list projection with SQLite mode=ro/query_only, reports its snapshot timestamp, and prints counts, paid steps and cost/time assumptions. Projection counts may lag the live database. Estimates use configured Jev pricing and cost_tracking.token_cost for embeddings; they exclude shared context preparation, advisory classification and deep assessment. Fetch successes can add screenable jobs. Timeout sums are scenario estimates, not expected runtime; each batch stops cooperatively at the two-hour deadline.

After the updated server has been loaded, explicitly submit one bounded repair batch:

```powershell
.venv\Scripts\python.exe -m scripts.process_discovery_backlog --execute
```

This sends a guarded POST to /api/discovery/backlog and returns immediately. Inspect Discovery runs for results before requesting another batch. It can spend money on uncached JD/context embeddings and Jev screening. It honors scan exclusivity, existing failure cooldowns and cancellation. No repair is dispatched by previewing estimates.

## Evidence review questions (October 2, 2026)

Completed, current reports derive a compact review checklist from saved judgments and pursuit constraints. This adds no model call, scoring weight, or new candidate fact. Strong transferable evidence is not a gap. Insufficient or low-confidence evidence is missing documentation; supported partial evidence is an experience coverage gap, not proof of missing ability. Ambiguous requirements stay unresolved. Only a sourced incompatible pursuit constraint is displayed as an explicit conflict. Unknown sponsorship stays a question. Core work and explicit requirements precede preferred qualifications.

The checklist starts collapsed, shows three points when opened, and retains JD/evidence quotes. Questions open the existing shared review dialog without overwriting the user's draft. Preference conflicts open pursuit decision controls rather than a story addition. Leaving questions unanswered changes no saved score. Outdated/incomplete reports do not show the current checklist.

Design references reviewed: [ResumeSkills JD analyzer](https://github.com/Paramchoudhary/ResumeSkills/blob/main/skills/job-description-analyzer/SKILL.md), [JobSentinel research notes](https://github.com/cboyd0319/JobSentinel/blob/main/docs/research/semantic-resume-job-matching.md), [ai-cv-job-matcher](https://github.com/lamanIbrahimli/ai-cv-job-matcher), and [resume-rag](https://github.com/atharvguitarist/resume-rag). These informed the comparison of evidence, uncertainty and related-versus-equivalent work. No external code or prompts were copied or installed. Repository popularity is not validation of scoring. We did not adopt frequency-based mandatory requirements, high-match overqualification labels, or inferred hiring probabilities. Run `npm run test:gaps` in frontend for deterministic classification regressions.

Reviewed September 30, 2026. Collection is local and uses no language model or Firecrawl.

- **YC:** now uses an app-owned, authenticated Microsoft Edge session for the Work at a Startup Product / Fulltime directory. Connect YC in Discovery runs; login is required. The old public category adapter remains in source for reference but is no longer selected by the live default collector. Browser session state is private and never copied from Codex.
- **LinkedIn:** public website search, not a supported developer API. Requests Product Manager jobs with Past 24 hours and each configured preferred country (up to three). Reads at most the first 60 cards per country; coverage is partial and not guaranteed newest-first despite the requested sort. A missing filter confirmation, login page, or access denial is reported as an error rather than zero jobs. No login, private endpoint, or account cookies are used. Rounded ages are stored with observation time and conservative expiry bounds: an ambiguous boundary is excluded from Source reports <24h. Calendar date and original relative label remain visible. First detection never substitutes for posting time. Descriptions use the existing verified extraction/paste workflow. Scheduled checks include LinkedIn; Check LinkedIn now runs only this source.
- **Built In:** reads public product-category listing pages and JobPosting JSON-LD from individual listings. Up to ten category pages and 100 details. Coverage remains partial; calendar dates do not prove a precise 24-hour publication window.
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
# Jev role classification (October 2, 2026)

Jev classifies the actual function and work focus after descriptions are retrieved, before the existing automatic assessment queue. It is initially **advisory**: it does not change keyword priorities, eligibility, evidence scores, or automatic paid-analysis decisions. Validate it against saved jobs and user corrections before changing that policy. A high classification confidence is not candidate fit.

The rubric, labels, pinned model, display threshold and published price are in `criteria/discovery-role-classification.json`. Only the listing title and description go to TypeSafe; candidate stories and preferences are not sent. Results are stored in the local discovery SQLite database by a hash of the input and rubric. Refreshing the page reads the cache and never calls Jev. Changing the JD or rubric invalidates the lookup; an upcoming scan classifies the new version. Changing candidate context alone does not require a new role classification. Short/missing descriptions and oversized inputs are skipped; known outside-location jobs are skipped. Low-confidence labels display as Unclear. Raw answers, model version, token usage, estimated cost and latency remain saved for evaluation.

Setup: create an account/API key in the [official TypeSafe console](https://console.typesafe.ai), then run `./scripts/connect-jev.ps1` in PowerShell. The hidden prompt saves the key to the Windows user environment, not the repository. Environment variables are not a credential vault; protect access to your Windows account. Restart the local server only after active scan/analysis work finishes. The next scan uses Jev. On other operating systems, set `TYPESAFE_API_KEY` before launching the server. OpenAI billing is separate.

Run `.venv/Scripts/python.exe -m scripts.benchmark_jev` from the product directory for a six-case synthetic live smoke test. It makes paid Jev calls and saves results under ignored `.local/jev-benchmark`; it is not a substitute for evaluation on actual jobs. Automated unit tests use mocked responses, so passing them does not establish model accuracy. Missing credentials and provider failures are visible on Discovery runs. A provider failure stops Jev calls for that scan, while existing local discovery and assessment continue. No immediate retries or automatic fallback model calls are made.

API contract: [TypeSafe HTTP reference](https://docs.typesafe.ai/api). Model and price: [TypeSafe models](https://docs.typesafe.ai/models). Uses the existing HTTP client; no third-party code was copied and no extra SDK is required. The discovery scheduler owns this step; it does not require introducing a second LangGraph solely for a single classification call.
