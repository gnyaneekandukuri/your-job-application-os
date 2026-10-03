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

Set `TYPESAFE_API_KEY` for Jev classification and assessment, and `OPENAI_API_KEY` for API embeddings. Preparation embeds only new or changed original text with `text-embedding-3-small`; vectors and sources are stored locally. Discovery compares a compact source-backed candidate profile and preferences with the JD, using keywords plus Jev, without embeddings. Only High priority jobs automatically enter deeper semantic assessment; other jobs can be assessed manually. Generative OpenAI calls remain disabled. See [the assessment design](docs/jev-assessment.md). Resume tailoring is not implemented. Never commit keys or personal data.

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
