# Google Sheets application tracker

Applied jobs supports newest/oldest applied-date sorting and Export all to Excel. Google Sheets is an explicit, one-way copy of all application tracker rows; it is not a second application database.

## Connect once

1. In Google Cloud, enable the Google Sheets API in your project.
2. Configure Google Auth Platform. For a personal app in testing, add your Google account as a test user.
3. Create an OAuth client of type **Desktop app**, download its JSON, and upload it in Applied jobs → Set up Google Sheets.
4. Press Connect Google and approve access in Google's sign-in page. The local server must be reachable at `http://127.0.0.1:8000`. The OAuth callback is `/api/applications/sheets/callback` on that origin.
5. Return to Applied jobs. Paste the URL of a spreadsheet this account can edit and save the destination.
6. Press Sync to Google Sheets. The app creates a **Job Review applications** tab without changing other tabs.

The existing-spreadsheet integration requests Google's `spreadsheets` scope, which grants access to all spreadsheets accessible to that account; the app restricts its operations to your selected spreadsheet. OAuth uses state and PKCE. Client configuration and tokens are saved only under the ignored `.local/personal` directory. Disconnect locally removes the local tokens and destination; to revoke Google's grant too, use your Google Account's connected-app settings. Testing-mode authorizations may expire and require reconnection.

## Sync behavior

The sheet receives Application ID, company, role, application date, status, location, job link, resume version identifier, follow-up date, notes, and job board. It does not receive resume files, stories, evidence, credentials, or full job descriptions. No AI calls are made.

Each sync reads the destination's Application IDs and updates matching rows, adding only new IDs. Sorting rows in Sheets is supported. Retrying an uncertain write re-reads IDs before writing. Keep the ID column and headers intact; duplicate IDs or unexpected headers stop sync. Values are written as RAW text, so notes starting with `=` are not executed as formulas. The app owns columns A–K; edits to those fields in Sheets are overwritten by the app on sync. Extra columns are preserved. Avoid editing/moving sheet rows during a sync. There is no automatic deletion or scheduled sync.

## Validation

Tests use simulated Google responses, covering OAuth state/PKCE, token refresh, destination validation, schema conflicts, retries, repeated sync, and tab isolation. Real account sign-in and live Google writes require the user's setup and are not exercised by automated tests.

Sources: https://developers.google.com/identity/protocols/oauth2/native-app and https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate
