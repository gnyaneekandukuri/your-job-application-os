"""Collect via the site's own signed-in UI; never replay gated API requests."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .yc_connection import save_status
from .community_sources import lenny_rows

HOME = 'https://www.lennysjobs.com/'
SEARCH = 'https://arc.trueup.io/jobs/search/member'
PROFILE = 'normal-profile'


def manual_login(root):
    """User-controlled supported browser, with no Playwright/debugging connection."""
    candidates = [Path(os.environ.get(key, '')) / 'Microsoft/Edge/Application/msedge.exe'
                  for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise ValueError('Microsoft Edge is not installed.')
    profile = (root / PROFILE).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    save_status(root, 'WAITING_FOR_LOGIN',
                'Sign in to Lenny’s in the normal Edge window, then close that window. The app will check Past day access afterward.')
    browser = subprocess.Popen([str(executable), f'--user-data-dir={profile}',
                                '--no-first-run', '--disable-background-mode', HOME],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        browser.wait(timeout=900)
    except subprocess.TimeoutExpired:
        # Do not terminate a window where the user may still be entering credentials.
        save_status(root, 'LOGIN_REQUIRED', 'Close the Lenny sign-in window, then click Check connection.')
        return False
    return True


def past_day(page):
    return page.get_by_role('radio', name=re.compile(r'^Past day'))


def ready(page):
    return (urlsplit(page.url).hostname == 'www.lennysjobs.com'
            and past_day(page).count() == 1 and past_day(page).is_enabled())


def result_page(data):
    results = data.get('results', [])
    if not results or not isinstance(results[0].get('hits'), list):
        raise ValueError('Lenny search response changed; no collection was confirmed.')
    result = results[0]
    total = result.get('nbHits', result.get('found'))
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ValueError('Lenny search total could not be verified.')
    return result['hits'], total


def response_for(page, action):
    with page.expect_response(lambda r: r.url == SEARCH, timeout=45000) as event:
        action()
    response = event.value
    if response.status != 200:
        raise ValueError('Lenny search failed; reconnect to check access.')
    return response.json()


def collect_pages(page, first):
    jobs, warnings = {}, []
    data = first
    for _ in range(100):
        hits, total = result_page(data)
        before = len(jobs)
        for hit in hits:
            if not hit.get('job_id'):
                raise ValueError('Lenny returned a listing without an identifier.')
            jobs[str(hit['job_id'])] = hit
        if len(jobs) >= total:
            break
        if len(jobs) == before:
            warnings.append('Repeated search results; remaining results could not be collected.')
            break
        # Sidebar also has Show more buttons; only the large button pages jobs.
        more = page.locator('button[data-size="lg"]').filter(has_text=re.compile(r'^Show more$'))
        try:
            more.wait_for(timeout=15000)
        except Exception:
            warnings.append('Results pagination did not become available.')
            break
        if more.count() != 1 or not more.is_enabled():
            warnings.append('More results reported, but pagination was unavailable.')
            break
        data = response_for(page, more.click)
    else:
        warnings.append('Reached 100-page safety limit; some results remain unchecked.')
    if not ready(page) or not past_day(page).is_checked():
        raise ValueError('Past day access changed during collection; results were not imported.')
    rows = lenny_rows({'results': [{'hits': list(jobs.values())}]})
    for row in rows:
        row['date_label'] = 'Lenny Past day filter; source update time, original posting date unverified'
        row['date_basis'] = 'source_updated_at'
    return rows, total, len(jobs), warnings


def run(command, root):
    if command == 'login':
        if not manual_login(root):
            return {'error': 'Close the sign-in window, then check the connection.', 'source_state': 'login_required'}
        command = 'check'
    from playwright.sync_api import sync_playwright, expect
    from .discovery_location import COUNTRIES
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(root / PROFILE),
                    channel='msedge', headless=True, accept_downloads=False)
        try:
            context.set_default_timeout(20000)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(HOME, wait_until='domcontentloaded', timeout=45000)
            past_day(page).wait_for(timeout=30000)
            # The radio renders disabled before the saved account finishes loading.
            # Wait for entitlement hydration instead of treating initial HTML as logout.
            try:
                expect(past_day(page)).to_be_enabled(timeout=30000)
            except AssertionError:
                pass
            if not ready(page):
                save_status(root, 'LOGIN_REQUIRED', 'Past day is locked or the session expired. Connect Lenny’s and verify account access.')
                return {'error': 'Lenny Past day access requires sign-in or an eligible account.', 'source_state': 'login_required'}
            if command in ('login', 'check'):
                save_status(root, 'CONNECTED', 'Past day access verified. Ready for scheduled collection.')
                return {'connected': True}
            options = json.loads((root / 'scan-options.json').read_text(encoding='utf-8'))
            names = {c['code']: c['name'] for c in COUNTRIES}
            countries = options.get('countries', [])
            if not countries or any(c not in names for c in countries):
                raise ValueError('Choose preferred countries in Your context before scanning Lenny’s.')
            # A fresh page has the site's Product Management default, which we verify.
            page.get_by_role('button', name=re.compile(r'^Job category Product Management')).wait_for()
            location = page.get_by_role('button', name=re.compile(r'^Location '))
            location.click()
            for country in dict.fromkeys(countries):
                name = names[country]
                page.get_by_role('searchbox', name='Search locations', exact=True).fill(name)
                choice = page.get_by_role('checkbox', name=re.compile(re.escape(name) + r'\s+[\d,.Kk+]+$'))
                if not choice.is_checked():
                    response_for(page, choice.click)
                    expect(choice).to_be_checked(timeout=15000)
            location.click()
            save_status(root, 'SCANNING', 'Collecting Product Management jobs with Past day and preferred-country filters...')
            if past_day(page).is_checked():
                page.get_by_role('radio', name='All', exact=True).check()
            first = response_for(page, lambda: past_day(page).check())
            rows, total, loaded, warnings = collect_pages(page, first)
            coverage = (f'Signed-in Product Management, Past day, countries {", ".join(countries)}: '
                        f'{loaded}/{total} search results read; {len(rows)} matching roles. '
                        'Source update dates are not verified original posting dates. '
                        'Full descriptions are fetched separately. '
                        + ('Partial coverage; see warnings.' if warnings else 'All filtered results collected.'))
            save_status(root, 'CONNECTED', f'Last collection: {loaded}/{total} results read.')
            return {'rows': rows, 'coverage': coverage, 'warnings': warnings}
        finally:
            context.close()


def main():
    command, root = sys.argv[1], Path(sys.argv[2])
    root.mkdir(parents=True, exist_ok=True)
    try:
        result = run(command, root)
    except Exception:
        # Never persist provider responses, cookies, tokens, or account-page contents.
        message = 'Lenny browser operation could not be verified. Reconnect and check Past day access; no unverified results were imported.'
        save_status(root, 'ERROR', message)
        result = {'error': message, 'source_state': 'error'}
    (root / 'result.json').write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    main()
