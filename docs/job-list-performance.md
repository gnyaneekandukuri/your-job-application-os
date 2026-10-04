# Fast job-list reads

The UI uses a local **materialized read model** and **TanStack Query v5**. It does not rebuild assessment reports when the user opens a list. No paid provider calls are made by the read-model builder or the list endpoints.

## Read path

- `GET /api/discovery/status`: small revision, freshness, error and scan-state heartbeat.
- `GET /api/discovery/page`: SQLite filtering, stable ordering, total count and a bounded page (25 by default; maximum 100). Filters apply to all saved opportunities before pagination. Complete assessments remain excluded from New jobs, as before.
- `GET /api/discovery/{id}/detail`: original description and company evidence, fetched only when a user expands details or reviews a JD.
- `GET /api/assessments/saved`: saved assessment summaries. Original report/evidence endpoints remain authoritative when opening a role or submitting a decision.

TanStack Query keys include filters, page, and the saved revision. It retains the previous page during a fetch, cancels unused requests, and caches revisited pages. Search is debounced by 250 ms. A four-second, visibility-aware heartbeat checks for revisions; unchanged results do not trigger another full list download. Selection remains in the component across pages. No automatic paid work is triggered by viewing a page.

The application no longer fetches every saved role review on opening New jobs. It fetches one role on navigation to that role. The older Saved roles screen and compatibility endpoints still use their original read paths.

## Projection lifecycle and correctness

`app/job_list.py` maintains the disposable `.local/<workspace>/job-list.sqlite3`. Source jobs, assessments and personal files remain authoritative and unchanged. The file is ignored by Git.

A single background worker checks source SQLite and WAL file metadata, workspace document metadata, YC connection status, queued jobs, and provider availability every two seconds. It rebuilds after changes. A minute bucket also refreshes time-sensitive posting-age filters. This detects background worker writes and edits made outside HTTP requests.

The builder records the input fingerprint **before** reading sources, then publishes all rows and metadata in one SQLite transaction. Readers see a complete prior or new generation. A source change during the build leaves the published generation marked as refreshing until the next build. A failed publication rolls back; the previous generation remains available and a visible error explains that updating failed. A first run without a projection shows a preparation state, not an empty list of jobs.

This is eventual consistency: changes normally become visible after the background check, build, and browser heartbeat. The UI marks updating results and disables assessment/decision actions while it knows results are refreshing. Existing write endpoints still validate current hashes and versions; cached rows are never authority for a paid action.

The current builder rebuilds the whole projection after a detected source change. At the current hundreds-of-jobs scale that is about two seconds locally. It does not yet do incremental per-row projection updates. If the dataset grows substantially, introduce durable per-job revision/change events and incremental projection writes. Do not move assessment computation back into list requests.

Bump `VERSION` in `app/job_list.py` when changing projection semantics. A projection can be rebuilt from source stores; do not delete authoritative data to clear it. `create_app(data_dir=...)` leaves the worker disabled for deterministic tests; call `app.state.job_list.refresh()` explicitly in those tests. The default production app starts the worker independently of scheduled scans.

## Validation

`tests/test_job_list.py` covers bounded pages, search beyond page one, filter behavior, no recomputation on reads, stale detection, time expiry, restart reuse, changes during builds, failed refresh, transaction rollback, and detail/API validation. Existing discovery and assessment tests cover the unchanged classification and action logic.

Measured locally with 497 saved listings: the page API served 25 rows in about 60–65 ms and about 53 KB. The earlier complete-list response was about 4.6 MB and took several seconds. These are local observations, not performance guarantees; initial/background rebuilding is measured separately.

## Sources used

- [Microsoft Materialized View pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/materialized-view): derived read models, background refresh and atomic publication.
- [TanStack Query paginated queries](https://github.com/TanStack/query/blob/main/docs/framework/react/guides/paginated-queries.md): page keys, retained previous data and pending-page handling.
- [SQLite query planning](https://www.sqlite.org/queryplanner.html): indexed filtering and ordering. SQLite remains local; no Redis, cloud database or message broker is required for this implementation.
