"""App-owned browser worker. No personal-browser attachment or cookie export."""
import json
import re
import sys
import sqlite3
from datetime import datetime, timezone
import time
from pathlib import Path
from urllib.parse import urlsplit

from .yc_connection import DIRECTORY_URL, save_status
from .discovery_sources import relevant


LISTINGS_JS = r'''() => {
  const rows = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const u = new URL(a.href);
    if (u.hostname !== 'www.workatastartup.com' || !/^\/jobs\/\d+$/.test(u.pathname)) continue;
    const title = a.innerText.trim();
    if (!title || title === 'View job') continue;
    let card = a.parentElement, companyLink = null;
    while (card && card !== document.body) {
      companyLink = [...card.querySelectorAll('a[href]')].find(el => /^\/companies\/[^/]+$/.test(new URL(el.href).pathname) && el.innerText.trim() && !/^See all /i.test(el.innerText.trim()));
      if (companyLink) break;
      card = card.parentElement;
    }
    if (!companyLink || !card || card === document.body) continue;
    const lines = a.parentElement.parentElement.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
    const typeIndex = lines.indexOf('Fulltime');
    rows.push({source_id:u.pathname.split('/').pop(), title, url:u.origin+u.pathname,
      company:companyLink.innerText.trim().replace(/\s*\([A-Z]\d+\)$/, ''),
      company_url:companyLink.href, location:typeIndex > 0 ? lines[typeIndex-1] : '',
      sponsorship:lines.find(s=> /^(Will Sponsor|US Citizen|US Citizenship)/.test(s)) || ''});
  }
  const companies = new Set([...document.querySelectorAll('a[href]')]
    .map(a=>new URL(a.href).pathname).filter(p=>/^\/companies\/[^/]+$/.test(p) && !p.endsWith('/website')));
  const total = document.body.innerText.match(/Showing\s+(\d+)\s+matching startups/i);
  return {rows, loaded:companies.size, total:total ? Number(total[1]) : null, loading:/Loading matches|Loading companies|Loading jobs/i.test(document.body.innerText)};
}'''


def job_description(text):
    """Only retain the employer's description, not account navigation or other jobs."""
    match = re.search(r'(?im)^About the role\s*$', text)
    if not match:
        return ''
    body = text[match.end():]
    body = re.split(r'(?im)^Other jobs at\b|^Work at a Startup\s*$', body)[0].strip()
    return body[:50000] if len(body) >= 200 else ''


def directory_ready(page):
    return (urlsplit(page.url).hostname == 'www.workatastartup.com'
            and urlsplit(page.url).path == '/companies'
            and page.locator('a[href="/conversations"], a[href="https://www.workatastartup.com/conversations"]').count() > 0
            and re.search(r'Showing\s+\d+\s+matching startups', page.locator('body').inner_text()) is not None)


def cached_description(row, saved, current=None):
    current = current or datetime.now(timezone.utc)
    if not saved or any(row.get(k) != saved.get(k) for k in ('title','company','url')):
        return None
    stamp = saved.get('description_checked_at')
    try:
        age = (current - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None
    if 0 <= age < 4 * 3600 and len(saved.get('description','')) >= 200:
        return saved['description'], stamp
    return None


def saved_descriptions(root):
    path = root.parent / 'discovery.sqlite3'
    if not path.exists(): return {}
    db = sqlite3.connect(path,timeout=10)
    try:
        saved = {}
        for payload, seen in db.execute("SELECT o.payload,o.last_seen FROM opportunities o JOIN sightings s ON o.id=s.opportunity_id WHERE s.board='yc'"):
            item = json.loads(payload)
            # One-time migration for descriptions verified before timestamps were stored.
            item.setdefault('description_checked_at', seen)
            saved[item.get('url')] = item
        return saved
    finally:
        db.close()


def job_heading_matches(headings, row):
    return any(row['title'].casefold() in heading.casefold() and row['company'].casefold() in heading.casefold() for heading in headings)


def directory_settled(snapshot, empty_checks):
    # A zero placeholder is not evidence of an empty search result.
    return snapshot['loaded'] > 0 or (snapshot['total'] == 0 and empty_checks >= 10 and not snapshot.get('loading'))


def run(command, root):
    from playwright.sync_api import sync_playwright
    profile = root / 'profile'
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile), channel='msedge',
                    headless=command != 'login', accept_downloads=False)
        try:
            context.set_default_timeout(15000)
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(DIRECTORY_URL, wait_until='domcontentloaded', timeout=45000)
            if response and response.status >= 400:
                from .discovery_network import SourceResponseError
                raise SourceResponseError(response.status, 'www.workatastartup.com', '/companies', response.headers)
            if command == 'login':
                save_status(root,'WAITING_FOR_LOGIN','Sign in to YC in the separate browser window. It will close after the directory is verified.')
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    if page.is_closed():
                        return fail(root,'LOGIN_REQUIRED','The sign-in window closed before the directory was verified.','login_required')
                    if directory_ready(page): break
                    if urlsplit(page.url).hostname == 'www.workatastartup.com' and page.locator('a[href="/conversations"]').count():
                        page.goto(DIRECTORY_URL,wait_until='domcontentloaded')
                    page.wait_for_timeout(1500)
                else:
                    return fail(root,'LOGIN_REQUIRED','Sign-in timed out. Connect YC again.','login_required')
            else:
                try:
                    page.get_by_text(re.compile(r'Showing\s+\d+\s+matching startups')).first.wait_for(timeout=15000)
                except Exception:
                    pass
            if not directory_ready(page):
                text = page.locator('body').inner_text()
                if urlsplit(page.url).hostname == 'account.ycombinator.com' or re.search(r'Log in|Sign in|Sign up free',text):
                    return fail(root,'LOGIN_REQUIRED','YC session is missing or expired. Sign in again.','login_required')
                return fail(root,'ERROR','YC directory could not be verified. No listings were imported.','error')
            if command in ('login','check'):
                save_status(root,'CONNECTED','Signed-in YC directory verified. Ready for scheduled collection.')
                return {'connected':True}
            save_status(root,'SCANNING','Collecting YC directory and job descriptions...')
            rows, warnings = {}, []
            total = None
            stalled = 0
            previous = -1
            loaded = 0
            deadline = time.monotonic() + 600
            empty_checks = 0
            for _ in range(50):
                snapshot = page.evaluate(LISTINGS_JS)
                total, loaded = snapshot['total'], snapshot['loaded']
                for row in snapshot['rows']:
                    if relevant(row['title']): rows[row['source_id']] = row
                # React initially renders 0 before the directory request resolves.
                if loaded == 0:
                    empty_checks += 1
                    if not directory_settled(snapshot, empty_checks):
                        if empty_checks >= 25:
                            return fail(root,'ERROR','YC directory did not finish loading. No zero-result scan was assumed.','error')
                        page.wait_for_timeout(1800)
                        continue
                if total is not None and loaded >= total: break
                stalled = stalled+1 if loaded == previous else 0
                if stalled >= 3 or time.monotonic() > deadline:
                    warnings.append('Directory stopped loading before all matching startups were verified.'); break
                previous = loaded
                page.keyboard.press('Control+End')
                page.wait_for_timeout(1800)
            else:
                warnings.append('Directory scrolling limit reached.')
            save_status(root,"SCANNING",f"Loaded {loaded}/{total} YC startups and {len(rows)} product roles. Reading job descriptions...")
            detail = context.new_page()
            cached = saved_descriptions(root)
            detail_requests = 0
            for index, row in enumerate(rows.values()):
                if index % 10 == 0:
                    save_status(root,"SCANNING",f"Prepared {index}/{len(rows)} product roles; requested {detail_requests} descriptions.")
                row.update(description='', posted_at=None, date_basis='unknown', funding='Unknown',
                           date_label='Posting date unavailable', collection_method='authenticated_browser')
                recent = cached_description(row, cached.get(row['url']))
                if recent:
                    row['description'], row['description_checked_at'] = recent
                    continue
                if detail_requests >= 120 or time.monotonic() > deadline:
                    continue
                detail_requests += 1
                try:
                    response = detail.goto(row['url'],wait_until='domcontentloaded',timeout=20000)
                    if response and response.status >= 400:
                        warnings.append(f"Job {row['source_id']}: HTTP {response.status}; description unavailable.")
                        continue
                    if urlsplit(detail.url).hostname == 'account.ycombinator.com' or urlsplit(detail.url).path == '/':
                        return fail(root,'LOGIN_REQUIRED','YC session expired during collection. Sign in again.','login_required')
                    detail.locator('h1').first.wait_for(state='visible')
                    headings = detail.locator('h1').all_inner_texts()
                    if not job_heading_matches(headings, row):
                        raise ValueError('Detail identity changed')
                    row['description'] = job_description(detail.locator('body').inner_text())
                    if row['description']:
                        row['description_checked_at'] = datetime.now(timezone.utc).isoformat()
                except Exception as exc:
                    warnings.append(f"Job {row['source_id']}: {type(exc).__name__}; description needs review.")
            missing = sum(not r['description'] for r in rows.values())
            if missing: warnings.append(f'{missing} listings need a full description.')
            if total is None or loaded < total:
                warnings.append(f'Partial directory: {loaded} startups loaded; reported total {total if total is not None else "unknown"}.')
            if not rows and (total is None or total > 0):
                return fail(root,'ERROR','YC showed startups but no product listings could be parsed. The collector needs review.','error')
            save_status(root,'CONNECTED','YC collection finished; see Discovery runs for coverage.')
            # Company cards may hide additional roles behind See all jobs. Do not claim exhaustive coverage.
            coverage = f'Authenticated YC directory: {loaded}/{total} startups loaded; {len(rows)} product roles. Partial coverage: company cards may omit roles. Posting dates are not verified.'
            return {'rows':list(rows.values()),'coverage':coverage,'warnings':warnings}
        finally:
            context.close()


def fail(root, state, message, source_state='error'):
    save_status(root,state,message)
    return {'error':message,'source_state':source_state}


if __name__ == '__main__':
    command, directory = sys.argv[1:]
    if command not in ('login','check','scan'): raise ValueError('Unsupported operation')
    root = Path(directory)
    try:
        result = run(command,root)
    except ImportError:
        result = fail(root,'SETUP_REQUIRED','Install requirements-yc.txt to enable the YC browser.')
    except Exception as exc:
        result = fail(root,'ERROR','YC browser operation failed. Ensure Microsoft Edge is installed, close any previous YC connection window, and reconnect.', getattr(exc, 'source_state', 'error'))
        result['diagnostic'] = getattr(exc, 'discovery_diagnostic', dict(kind=type(exc).__name__, reason='YC browser operation failed; see exception type.',
                                    host='www.workatastartup.com', path='/companies', http_status=None, attempts=1, causes=[]))
    (root / 'result.json').write_text(json.dumps(result),encoding='utf-8')
