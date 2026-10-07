# Job Review

A local product-management job discovery and evidence review app. React and TypeScript provide the interface; FastAPI, Pydantic, LangGraph, and SQLite run locally. This repository contains no candidate profile, resume, stories, databases, or prior personal Git history.

## Run locally

Requires Python 3.12 and Node.js with npm.

1. Create a Python environment: `python -m venv .venv`.
2. Activate it and run `pip install -r requirements.txt`.
3. In `frontend`, run `npm ci` and `npm run build`.
4. From the repository root, run `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
5. Open http://127.0.0.1:8000, choose your target roles and countries, then add your own documents or stories in **Your context library**.

Discovery is initially unscheduled; enable it in Discovery runs when ready. Paid automatic assessment is initially disabled. Board collection currently focuses on product-management roles, with partial coverage and source-dependent posting dates. It is not a universal job search engine.

## Personal data and model access

First startup creates an empty `.local/` directory containing SQLite databases and `.local/workspace/` containing your preferences, context controls, and personal copies of scoring criteria. Uploaded documents and stories also stay under `.local/`. Git excludes this directory. Back it up separately if needed; GitHub is a code backup, not a backup of your analysis history.

Set `JOB_REVIEW_DATA_DIR` to an absolute private directory to choose another location. Run one server process per data directory. Do not point the public product at an older personal workspace without an explicit migration.

Set `TYPESAFE_API_KEY` for Jev classification and assessment, and `OPENAI_API_KEY` for API embeddings, candidate skills extraction, and explicitly requested resume tailoring. Original source embeddings are reused until the source changes. Generative OpenAI calls are disabled for role assessment; resume drafting and claim checking use a separate generation-enabled adapter and the same local usage ledger. Never commit keys or personal data.

Your canonical resume is optional for evidence review. To register a separate master for future tailoring, place it under `.local/workspace/sources/master/` and set `source` in `.local/workspace/knowledge/canonical_resume_inventory.json` to its relative path. Uploading a resume to the context library supplies evidence, but does not silently make it an approved canonical master. Tailoring and application submission are not automated.

Generic defaults live in `criteria/`. Feedback changes the user's local copies, never the shared defaults. Work authorization starts unspecified. Current structured authorization checks specifically cover H-1B transfers; other authorization situations require manual review.

## Verify

Run `python -m pytest tests -q` and `npm run build` in `frontend`. Tests use fictional inputs, temporary directories, and no paid model calls.

## Migrate an existing local workspace

Stop both app servers. Run `python scripts/migrate_workspace.py LEGACY_ROOT .local/personal` from this repository. The destination must not exist. The script copies user sources and local uploads, backs up all top-level SQLite databases, verifies database integrity and row counts, and checks copied file hashes. It leaves the source intact. Write `personal` into `.local/active-workspace` to select the migrated directory on future starts, or set `JOB_REVIEW_DATA_DIR` explicitly. The selection file and all migrated data are ignored by Git. The Windows launcher is `./scripts/start-review.ps1`.

Make future feature changes in this repository, test, then commit and push only code. Back up `.local` separately for personal history. Do not resume the legacy app after cutover unless deliberately restoring that older snapshot.



### Connect YC / Work at a Startup

Install Microsoft Edge and the optional browser dependency:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-yc.txt
```

In **Discovery runs**, choose **Connect YC**. Sign in in the separate Edge window; the window closes when the authenticated Product / Fulltime directory is verified. Use **Check connection** to verify the saved session, then **Check YC now** to collect only YC. **Check now** still checks all enabled sources. Scheduled scans use the same private browser profile while the backend is running. A login in Codex or your regular browser is separate and is not copied.

The profile is saved under the active private data directory, `yc-browser/profile`, and is excluded from Git. Treat that directory as sensitive login data; do not share or upload it. Browser state is saved by Edge, not exported into an API response. The app never asks for your password. If YC expires the session, Discovery runs displays **Sign in again**. An access denial remains an access denial; the app does not bypass it or silently substitute the public YC collector.

The collector scrolls the directory and reads linked job descriptions. Coverage remains partial because company cards can omit roles; the scan reports loaded startups, warnings, and missing descriptions. No posting dates are inferred from sort order or first discovery. YC's sponsorship labels are retained as source claims, not proof of H-1B transfer eligibility. Existing YC IDs retain their history across the public-site to authenticated-directory transition.

### Additional public sources

Full discovery runs also check HN's official hiring-thread API, Lenny's Jobs, and Cosign. HN collection looks for explicit PM titles in top-level comments from the latest hiring thread posted within 24 hours (up to 400 comments). That is the hiring comment's date, not the employer's original vacancy date. Informal titles may be missed.

Lenny's uses a fresh anonymous Edge session with the optional Playwright dependency above. It reads only the first publicly available PM results page. Its gated date filter is not used, and update timestamps are not treated as posting dates. Cosign supplies only its public subset; fuller account-only results are not collected. These sources report partial coverage, including when no PM listings appear in their public preview. Missing JDs enter the existing description-retrieval flow.

Breakout List companies, Ramp vendor-report links, and Next Play's public RSS issues appear under **Discovery runs → Company and newsletter leads**. They refresh during full scans, stay in the local `source-leads.sqlite3` database, and never automatically become jobs or trigger model calls. Failed reads retain the last successful results with a visible error. Newsletter publication dates are not job-posting dates; paid/private issue content is not fetched. These connectors do not yet extract individual openings from newsletters or monitor each listed company's career site.

Worth checking roles in New opportunities can be selected with checkboxes and submitted together using **Analyze selected**. The local durable queue runs them one at a time after any active scan. Full JDs are required; changed listings must be selected again. Pending selections survive a restart; interrupted or failed paid attempts require an explicit retry. Costs appear by role under API costs (outside automatic scans).


### Reusable candidate skills profile

Your context library includes a separate skills profile. Saving an experience starts skills preparation, including on first use. You can also
prepare existing context explicitly. Later updates are incremental while the local server is running. It reads current firsthand
stories/raw evidence and the current master resume; coaching guidance is excluded.
A resume uploaded in this panel replaces the profile's resume input only, not the
master resume. LinkedIn links can import explicit public skills metadata when
available; login-only pages need pasted skills or an uploaded profile export.
Pasted LinkedIn skills and manual additions are listed claims and make no model call.

OpenAI extracts unrestricted skill names, equivalent names, work descriptions and
exact source quotes from bounded passages. A separate cached consolidation pass
merges equivalent labels. Extraction does not certify competence or approve resume
claims. Changed passages are processed incrementally; removed/replaced source
passages disappear from the active view. Corrections and hidden skills persist.

Requests use the existing Responses provider and shared usage ledger under
`candidate_skills`, with `store=false`. Assessment generation remains disabled.
Errors stop automatic preparation until the user resumes; completed passages are
reused. A server interruption requires explicit resume, avoiding silent repeated
billing. The profile can be inspected without triggering paid requests. Local
profile state, uploaded resume text and model outputs live in the private workspace,
not Git. Discovery now uses this profile for Jev matching against JD responsibilities.
Role preferences are checked separately; exact keywords and legacy weights do not
decide priority. High priority requires confident role and skills alignment with a
specific skills-fit judgment. Individual skills are classified independently for
explanation; their ranking or selection confidence cannot block High priority. Hidden skills are excluded; profile edits invalidate cached
initial verdicts. Refreshing the page never makes paid calls. Existing listings are
rechecked when included in a subsequent scan. Preferences remain in Your preferences,
separate from the skills and experience library.

### Evidence-based pursuit decisions

The initial screening policy is in `criteria/discovery-screening.json`. All active
skills participate, in request-size-bounded batches. Role fit and overall skills
fit decide priority; independent direct/transferable skill matches explain it.

`criteria/pursuit-policy.json` defines application-effort rules. Casual requires
at least 50/100 documented fit, a non-provisional score, no more than 50% of core
requirements with insufficient evidence, and established target-work alignment.
Otherwise the report says **Review evidence**, rather than automatically endorsing
an application or rejecting the candidate. Explicit constraint conflicts still
recommend Skip. Attack Hard retains its stricter dimension and core-evidence rules.
These are editable, uncalibrated product defaults, not hiring probabilities.
Existing workspace migrations add the baseline gates while preserving custom
Attack Hard thresholds. Reports requiring review appear under Analyzed jobs >
Unfinished / needs review and All analyzed; candidates retain their apply/pass choice.

### Keep the local scheduler available on Windows

Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-server-task.ps1` once to register a current-user Windows task. It starts the server at sign-in and checks every five minutes, without restarting an existing server. It runs while that user is signed in, including on battery; it does not wake a sleeping laptop. Scan times and paid-analysis choices remain controlled by Discovery runs. Startup/exit diagnostics are saved in `.local/server-supervisor.log`; API keys remain in your Windows user environment. Disable or remove **Job Review Local Server** in Windows Task Scheduler to stop automatic server startup.


## Resume tailoring

Open **Your jobs → Analyzed jobs → Tailor resume**, or use the button in a saved role's workspace. A current completed deep assessment and a base resume are required. The base can come from the master resume, the resume uploaded under Your context / Add skills, or pasted text for this role.

Choose PAR (default), XYZ, concise STAR, or your existing style. Optionally remember the choice for future jobs. **Prepare draft** explicitly sends the JD, base resume, selected original passages already retrieved during assessment, and candidate fact/conflict rules to OpenAI. It loads [the resume-tailoring skill](skills/resume-tailoring/SKILL.md) into both the drafting instructions and the separate claim-checking request. It does not send the entire story library, request new embeddings, or automatically tailor jobs during scans.

The server saves progress in the existing private SQLite cache. Reopening the page does not dispatch a new request. Identical successful inputs reuse the saved draft; failed or interrupted calls require explicit retry. A saved draft can resume at claim checking without repeating generation. API costs attribute drafting and claim checking to the role.

Each factual block needs exact source citations; numbers must occur in those citations. A separate model review checks support, ownership, scope, and conflicts. These checks are fallible and are not independent fact verification. Review wording and source excerpts, edit or remove flagged claims, then recheck edits. Approval is blocked until all current blocks pass checks and the candidate explicitly confirms review. Changing a draft removes its approval. Changed JD/source/policy inputs prevent approval or export of an outdated version.

Approved drafts can be downloaded as **plain text**. This first implementation does not produce Word/PDF layout exports or submit applications. It keeps the master resume unchanged. Model-call tests use an offline provider fixture; they validate orchestration and rejection paths, not production-model factual accuracy.

Resume drafts open in a local Tiptap document editor. Click and type in the resume, change paragraph styles, and use undo/redo. The evidence panel follows the selected paragraph and can be hidden. Save changes stores an unverified revision and revokes prior approval; Check changes runs the claim review before approval. New or unsupported wording can be saved while being corrected but cannot pass approval without a successful check. Closing with unsaved edits asks before discarding them. Downloads remain plain text.

Citation checks accept whitespace-only differences and recognize saved fact-rule claims by their exact source identifiers. Real wording mismatches remain flagged on the affected paragraph; valid paragraphs still receive the semantic claim check. Evidence metadata is retained through document edits and paragraph splits; edits never inherit verified status. The editor is loaded on demand and uses no external document-hosting service.
