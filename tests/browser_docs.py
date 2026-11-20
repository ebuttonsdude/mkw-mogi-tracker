"""Optional isolated Chromium check for an already-built documentation site.

Build with tools/build_docs.py first. Run this module in a separate PID namespace,
with the existing Playwright environment and PLAYWRIGHT_BROWSERS_PATH configured.
No tracker, hardware, clipboard, or real user data is used.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.local/site-public'))
    args = parser.parse_args()
    if not (args.directory / 'index.html').is_file():
        raise SystemExit('Build the documentation before running browser checks.')

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(args.directory)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    started = False
    try:
        worker.start()
        started = True
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                page.emulate_media(reduced_motion="reduce")
                errors = []
                external = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('response', lambda response: errors.append(f'HTTP {response.status}: {response.url}')
                        if response.status >= 400 else None)
                def local_only(route):
                    if urlsplit(route.request.url).hostname != '127.0.0.1':
                        external.append(route.request.url)
                        route.abort()
                    else:
                        route.continue_()
                page.route('**/*', local_only)
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                expect(page.locator('h1')).to_have_count(1)
                expect(page.locator('h1')).to_contain_text('Less scorekeeping')
                expect(page.get_by_role('link', name='Skip to content')).to_have_count(1)
                expect(page.locator('article img')).to_have_count(1)
                page.get_by_role('button', name='Search...', exact=True).click()
                page.get_by_role('combobox', name='Search...').fill('NVFP4')
                expect(page.locator('#hextra-search-results a').first).to_be_visible()
                page.keyboard.press('Escape')
                # Regression: picker belongs to the header, not the article.
                expect(page.locator('.hextra-nav-container #character-theme')).to_have_count(1)
                widths = []
                for width in (1280, 1440, 1920, 2560):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.evaluate('window.scrollTo(0, 0)')
                    left = page.locator('.hextra-sidebar-container').bounding_box()
                    right = page.locator('.hextra-toc').bounding_box()
                    assert abs(left['x']) <= 1, (width, left)
                    assert abs(right['x'] + right['width'] - width) <= 1, (width, right)
                    assert left['y'] + left['height'] >= 999, (width, left)
                    widths.append(page.locator('#content').bounding_box()['width'])
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                assert widths[-1] > widths[1] + 600, widths
                page.set_viewport_size({'width': 1440, 'height': 1000})
                for theme in ('conkdor', 'light'):
                    page.get_by_label('Website theme').select_option(theme)
                    styles = page.locator('#backToTop').evaluate("el => { const s = getComputedStyle(el.parentElement); return {shadow:s.boxShadow, background:s.backgroundColor, panel:getComputedStyle(el.closest('nav')).backgroundColor}; }")
                    assert styles['shadow'] == 'none', styles
                    assert styles['background'] == styles['panel'], styles
                page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight / 2)')
                left = page.locator('.hextra-sidebar-container').bounding_box()
                right = page.locator('.hextra-toc').bounding_box()
                assert left['y'] + left['height'] >= min(1000, right['y'] + right['height']) - 1, (left, right)
                page.evaluate('window.scrollTo(0, 0)')
                picker = page.get_by_label('Website theme')
                expect(picker.locator('option')).to_have_count(51)
                picker.select_option('luigi')
                expect(page.locator('html')).to_have_attribute('data-character', 'luigi')
                page.reload()
                expect(page.get_by_label('Website theme')).to_have_value('luigi')
                picker.select_option('light')
                expect(page.locator('html')).to_have_class(re.compile(r'(^| )light( |$)'))
                picker.select_option('mario')
                expect(page.locator('html')).to_have_class(re.compile(r'(^| )dark( |$)'))
                evidence = Path('.local/docs-evidence')
                evidence.mkdir(parents=True, exist_ok=True)
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(evidence / 'desktop.png'))
                picker.select_option('conkdor')
                for width in (540, 1920, 2560):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    if width < 768:
                        page.wait_for_function("document.querySelector('.hextra-sidebar-container').getBoundingClientRect().bottom <= document.querySelector('.hextra-nav-container').getBoundingClientRect().bottom + 1")
                    page.screenshot(path=str(evidence / f'layout-{width}.png'))
                picker.select_option('light')
                page.screenshot(path=str(evidence / 'layout-light.png'))
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
                expect(page.locator('#backToTop')).to_have_css('opacity', '1')
                left = page.locator('.hextra-sidebar-container').bounding_box()
                right = page.locator('.hextra-toc').bounding_box()
                assert abs(left['y'] + left['height'] - right['y'] - right['height']) <= 1, (left, right)
                page.locator('#backToTop').click()
                page.wait_for_function('window.scrollY === 0')
                page.goto(base + '/getting-started/')
                expect(page.locator('h1')).to_have_count(1)
                expect(page.locator('#content').get_by_role('link', name='Linux', exact=True)).to_be_visible()
                page.goto(base + '/install/linux/')
                arch = page.get_by_role('tab', name='Arch Linux', exact=True)
                expect(arch).to_have_attribute('aria-selected', 'true')
                expect(page.locator('#panel-fedora')).to_be_hidden()
                page.get_by_role('tab', name='Fedora', exact=True).click()
                expect(page.locator('#panel-fedora')).to_be_visible()
                expect(page.locator('[data-distro-note="fedora"]')).to_be_visible()
                page.evaluate("location.hash = 'start-the-tracker'")
                expect(page.get_by_role('tab', name='Fedora', exact=True)).to_have_attribute('aria-selected', 'true')
                expect(page.locator('[data-distro-note="fedora"]')).to_be_visible()
                page.get_by_role('tab', name='Fedora', exact=True).press('ArrowRight')
                expect(page.locator('#panel-ubuntu')).to_be_visible()
                expect(page.locator('[data-distro-note="fedora"]')).to_be_hidden()
                clone = 'git clone --branch main https://github.com/ebuttonsdude/mkw-mogi-tracker.git'
                expect(page.get_by_text(clone, exact=False)).to_have_count(1)
                for width in (320, 390, 540, 768, 1024):
                    page.set_viewport_size({'width': width, 'height': 844})
                    expect(page.get_by_label('Website theme')).to_be_visible()
                    page.get_by_label('Website theme').select_option('conkdor')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.set_viewport_size({'width': 390, 'height': 844})
                page.emulate_media(reduced_motion='reduce')
                page.get_by_role('button', name='Menu', exact=True).click()
                expect(page.get_by_role('button', name='Menu', exact=True)).to_have_attribute('aria-expanded', 'true')
                page.get_by_role('button', name='Menu', exact=True).click()
                page.wait_for_function("document.querySelector('.hextra-sidebar-container').getBoundingClientRect().bottom <= document.querySelector('.hextra-nav-container').getBoundingClientRect().bottom + 1")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
                page.screenshot(path=str(evidence / 'mobile.png'))
                assert not errors, errors
                assert not external, external
                print('PASS: documentation navigation, one H1, local search, header themes, edge-aligned fluid layout at320–2560px, full-height panels, themed scroll-to-top, mobile menu, no overflow or external runtime requests.')
            finally:
                browser.close()
    finally:
        try:
            if started:
                server.shutdown()
        finally:
            server.server_close()
            if started:
                worker.join(timeout=5)


if __name__ == '__main__':
    main()
