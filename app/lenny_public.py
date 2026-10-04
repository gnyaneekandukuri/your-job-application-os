"""Isolated read of Lenny's anonymous public page; JSON on stdout only."""
import json


def read():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page()
            with page.expect_response(lambda r: r.url == 'https://arc.trueup.io/jobs/search', timeout=45000) as response:
                page.goto('https://www.lennysjobs.com/', wait_until='domcontentloaded', timeout=45000)
            if response.value.status != 200:
                raise ValueError('Public search unavailable')
            return response.value.json()
        finally:
            browser.close()


if __name__ == '__main__':
    print(json.dumps(read()))
