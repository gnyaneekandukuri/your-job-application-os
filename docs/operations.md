# Local operations

[Back to the product overview](../README.md)

## Scan lifecycle, recovery and safe restarts

A scan finishes after collection, screening, and saving eligible jobs to the automatic assessment queue. Deep analysis continues separately and does not delay the next scheduled scan. Limited board coverage is informational; collection or screening failures make a scan incomplete. Historical scan records are preserved.

Scans recover transient free-source failures with bounded backoff and widen each source's next lookback after missed collection. A two-hour cooperative deadline abandons expired scans after sleep/suspension; a returning collector cannot continue into paid screening. Saved pending jobs are screened in bounded batches, active/newest first, including jobs absent from current search results. See [recovery and backlog operations](discovery-integrations.md#recovery-and-backlog-operations) for limits and a read-only repair preview.

On Windows, use `./scripts/restart-review-server.ps1` to activate server changes. It verifies the repository listener, creates `maintenance.flag` in the data directory (`.local/` or `JOB_REVIEW_DATA_DIR`), and polls `/api/maintenance/status` for up to 300 seconds (`-DrainTimeoutSeconds` overrides this). Draining prevents new scans and stops automatic, selected, and manual queues between jobs; an active paid request finishes normally. The script stops the listener only after the server acknowledges draining and reports no active work, then launches the supervisor and waits for readiness. It removes its flag on success, failure, or timeout; failures report Restart skipped. Other active writing/tailoring work also blocks restart. The supervisor leaves an existing listener alone. Crash/forced-termination recovery still marks unfinished scans interrupted and retains saved listings; phase replay is not supported.

## Migrate an existing local workspace

Stop both app servers. Run `python scripts/migrate_workspace.py LEGACY_ROOT .local/personal` from this repository. The destination must not exist. The script copies user sources and local uploads, backs up all top-level SQLite databases, verifies database integrity and row counts, and checks copied file hashes. It leaves the source intact. Write `personal` into `.local/active-workspace` to select the migrated directory on future starts, or set `JOB_REVIEW_DATA_DIR` explicitly. The selection file and all migrated data are ignored by Git. The Windows launcher is `./scripts/start-review.ps1`.

Make future feature changes in this repository, test, then commit and push only code. Back up `.local` separately for personal history. Do not resume the legacy app after cutover unless deliberately restoring that older snapshot.

## Keep the local scheduler available on Windows

Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-server-task.ps1` once to register a current-user Windows task. It starts the server at sign-in and checks every five minutes, without restarting an existing server. It runs while that user is signed in, including on battery; it does not wake a sleeping laptop. Scan times and paid-analysis choices remain controlled by Discovery runs. Startup/exit diagnostics are saved in `.local/server-supervisor.log`; API keys remain in your Windows user environment. Disable or remove **Job Review Local Server** in Windows Task Scheduler to stop automatic server startup.
