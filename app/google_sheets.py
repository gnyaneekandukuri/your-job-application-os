"""Explicit, one-way application sync. No model calls; credentials stay in private storage."""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from threading import RLock
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field
from .retrieval import RetrievalError

SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API = 'https://sheets.googleapis.com/v4/spreadsheets/'
TAB = 'Job Review applications'
HEADERS = ['Application ID', 'Company', 'Role', 'Applied date', 'Status', 'Location',
           'Job link', 'Resume version', 'Follow-up date', 'Notes', 'Job board']


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credentials_json: str = Field(max_length=12000)


class SheetTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(max_length=500)


def sheet_id(value):
    match = re.fullmatch(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/[^\s]*)?', value.strip())
    if not match:
        raise RetrievalError('Paste the Google Sheets spreadsheet URL.', 422)
    return match[1]


def application_row(row):
    return [row['id'], row['company'], row['title'], row['applied_date'], row['status'],
            row.get('location', ''), row.get('url', ''), row.get('resume_revision', ''),
            row.get('follow_up_date') or '', row.get('notes', ''), ', '.join(row.get('boards', []))]


def updates_for(rows, saved):
    """Re-read IDs on every sync, so row sorting and uncertain write outcomes are safe to retry."""
    if saved and saved[0] != HEADERS:
        raise RetrievalError('The sync tab has different headers. Restore its original headers or rename that tab before syncing.', 409)
    positions = {}
    for index, values in enumerate(saved[1:], 2):
        if not values or not any(str(v).strip() for v in values):
            continue
        key = str(values[0])
        if not key or key in positions:
            raise RetrievalError('The sync tab has missing or duplicate application IDs. Fix those rows before syncing.', 409)
        positions[key] = index
    writes = [] if saved else [{'range': f"'{TAB}'!A1:K1", 'values': [HEADERS]}]
    added = updated = 0
    next_row = max(2, len(saved) + 1)
    for row in rows:
        values = application_row(row)
        index = positions.get(row['id'])
        if index:
            previous = saved[index - 1]
            if previous + [''] * (len(HEADERS) - len(previous)) == values:
                continue
            updated += 1
        else:
            index = next_row
            next_row += 1
            added += 1
        writes.append({'range': f"'{TAB}'!A{index}:K{index}", 'values': [values]})
    return writes, added, updated


class GoogleSheets:
    def __init__(self, private_dir, applications, transport=None):
        self.path = private_dir / 'google-sheets-private.json'
        self.applications = applications
        self.transport = transport
        self.lock = RLock()
        self.pending = None
        from .sheets_schedule import SheetsSchedule
        self.schedule = SheetsSchedule(self) if hasattr(applications, 'path') else None

    def read(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RetrievalError('Google Sheets settings could not be read. Check local storage.', 409) from None

    def write(self, data):
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(data, output)
        os.replace(temporary, self.path)

    def status(self):
        with self.lock:
            data = self.read()
            return {'configured': bool(data.get('client')), 'connected': bool(data.get('tokens')),
                    'target': data.get('target'), 'last_sync': data.get('last_sync'),
                    'automatic_sync': self.schedule.status() if self.schedule else None}

    def configure(self, value):
        try:
            installed = json.loads(value.credentials_json)['installed']
            client = {k: installed[k] for k in ('client_id', 'client_secret')}
            if not isinstance(client['client_id'], str) or not client['client_id'].endswith('.apps.googleusercontent.com') or not isinstance(client['client_secret'], str) or not client['client_secret']:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RetrievalError('Upload the OAuth client JSON for a Google Desktop app, not a service account.', 422) from None
        with self.lock:
            data = self.read()
            if data.get('client') != client:
                self.write({'client': client})
                self.pending = None
            if self.schedule:self.schedule.arm()
            return self.status()

    def connect(self):
        with self.lock:
            data = self.read()
            if not data.get('client'):
                raise RetrievalError('Set up the Google OAuth client first.', 409)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            redirect = 'http://127.0.0.1:8000/api/applications/sheets/callback'
            self.pending = {'state': state, 'verifier': verifier, 'expires': time.time() + 600, 'redirect': redirect}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
                'client_id': data['client']['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
                'scope': SCOPE, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
                'access_type': 'offline', 'prompt': 'consent select_account'})}

    def token_request(self, fields):
        try:
            with httpx.Client(timeout=30, transport=self.transport) as client:
                response = client.post(TOKEN_URL, data=fields)
            if response.status_code != 200:
                raise RetrievalError('Google authorization expired or failed. Connect Google again. Check that the OAuth app is configured for your account.', 409)
            result = response.json()
            if not result.get('access_token'):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError):
            raise RetrievalError('Could not reach Google authorization. Try again.', 502) from None

    def callback(self, state, code, error=''):
        with self.lock:
            pending = self.pending
            if not pending or pending['expires'] < time.time() or not secrets.compare_digest(state, pending['state']):
                raise RetrievalError('This connection request expired. Return to Applied jobs and connect again.', 409)
            self.pending = None
            if error or not code:
                raise RetrievalError('Google connection was not approved. Your applications have not been sent.', 409)
            data = self.read()
            tokens = self.token_request({**data['client'], 'code': code, 'code_verifier': pending['verifier'],
                'redirect_uri': pending['redirect'], 'grant_type': 'authorization_code'})
            if SCOPE not in tokens.get('scope', '').split():
                raise RetrievalError('Google Sheets permission was not granted. Connect again and allow Sheets access.', 409)
            # A newly selected Google account must explicitly select its destination.
            self.write({'client': data['client'], 'tokens': {**tokens, 'expires_at': time.time() + tokens.get('expires_in', 3600)}})
            if self.schedule:self.schedule.arm()

    def access_token(self):
        data = self.read()
        tokens = data.get('tokens') or {}
        if not tokens:
            raise RetrievalError('Connect Google before syncing.', 409)
        if tokens.get('expires_at', 0) <= time.time() + 60:
            if not tokens.get('refresh_token'):
                raise RetrievalError('Google authorization expired. Connect again.', 409)
            refreshed = self.token_request({**data['client'], 'refresh_token': tokens['refresh_token'], 'grant_type': 'refresh_token'})
            data['tokens'] = {**tokens, **refreshed, 'expires_at': time.time() + refreshed.get('expires_in', 3600)}
            self.write(data)
            tokens = data['tokens']
        return tokens['access_token']

    def request(self, method, path, **kwargs):
        token = self.access_token()
        try:
            with httpx.Client(timeout=45, transport=self.transport) as client:
                response = client.request(method, API + path, headers={'Authorization': 'Bearer ' + token}, **kwargs)
            if response.status_code >= 400:
                messages = {401: 'Google authorization expired. Connect again.',
                    403: 'Google denied access. Enable the Sheets API and check that this Google account can edit the spreadsheet.',
                    404: 'Spreadsheet not found. Check the link and sharing access.',
                    429: 'Google is rate limiting requests. Wait briefly before syncing again.'}
                raise RetrievalError(messages.get(response.status_code, 'Google Sheets could not finish this request. Try syncing again; saved application IDs prevent duplicate rows.'), 502)
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise RetrievalError('Google Sheets did not confirm the result. Try syncing again; existing application IDs will be checked first.', 502) from None

    def set_target(self, value):
        with self.lock:
            sid = sheet_id(value.url)
            info = self.request('GET', sid, params={'fields': 'spreadsheetId,properties.title'})
            data = self.read()
            data['target'] = {'id': sid, 'title': info['properties']['title'], 'url': f'https://docs.google.com/spreadsheets/d/{sid}/edit'}
            data.pop('last_sync', None)
            self.write(data)
            if self.schedule:self.schedule.arm()
            return self.status()

    def disconnect(self):
        with self.lock:
            data = self.read()
            self.write({'client': data['client']} if data.get('client') else {})
            self.pending = None
            if self.schedule:self.schedule.arm()
            return self.status()

    def sync(self, automatic=False):
        if not self.lock.acquire(blocking=automatic):
            raise RetrievalError('Google Sheets is busy. Let the current request finish.', 409)
        try:
            if automatic and self.schedule and not self.schedule.status()['pending']: return self.status()
            data = self.read()
            target = data.get('target')
            if not target:
                raise RetrievalError('Choose your destination spreadsheet first.', 409)
            sid = target['id']
            info = self.request('GET', sid, params={'fields': 'sheets.properties'})
            sheet = next((s['properties'] for s in info.get('sheets', []) if s['properties']['title'] == TAB), None)
            if sheet is None:
                created = self.request('POST', sid + ':batchUpdate', json={'requests': [{'addSheet': {'properties': {'title': TAB, 'gridProperties': {'frozenRowCount': 1}}}}]})
                sheet = created['replies'][0]['addSheet']['properties']
            saved = self.request('GET', sid + '/values/' + quote(f"'{TAB}'!A:K", safe='')).get('values', [])
            seq, rows = self.schedule.snapshot() if self.schedule else (None, self.applications.list())
            writes, added, updated = updates_for(rows, saved)
            max_row = max([len(saved), 1] + [int(re.search(r'!A(\d+)', w['range'])[1]) for w in writes])
            grid = sheet.get('gridProperties', {})
            if max_row > grid.get('rowCount', 1000) or grid.get('columnCount', 26) < 11:
                self.request('POST', sid + ':batchUpdate', json={'requests': [{'updateSheetProperties': {
                    'properties': {'sheetId': sheet['sheetId'], 'gridProperties': {'rowCount': max(max_row, grid.get('rowCount', 1000)), 'columnCount': max(11, grid.get('columnCount', 26))}},
                    'fields': 'gridProperties.rowCount,gridProperties.columnCount'}}]})
            if writes:
                self.request('POST', sid + '/values:batchUpdate', json={'valueInputOption': 'RAW', 'data': writes})
            if max_row > 1:
                # Sort whole rows so user-added columns remain attached to the application.
                # ISO dates written as RAW strings sort chronologically. Keep the header fixed.
                self.request('POST', sid + ':batchUpdate', json={'requests': [{'sortRange': {
                    'range': {'sheetId': sheet['sheetId'], 'startRowIndex': 1, 'endRowIndex': max_row},
                    'sortSpecs': [{'dimensionIndex': 3, 'sortOrder': 'DESCENDING'},
                                  {'dimensionIndex': 0, 'sortOrder': 'DESCENDING'}]}}]})
            from datetime import datetime, timezone
            data = self.read()  # Preserve a token refreshed during the requests.
            data['last_sync'] = {'at': datetime.now(timezone.utc).isoformat(), 'added': added, 'updated': updated, 'total': len(rows)}
            self.write(data)
            if self.schedule:self.schedule.acknowledge(seq)
            return self.status()
        finally:
            self.lock.release()
