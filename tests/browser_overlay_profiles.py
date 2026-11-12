"""Optional isolated Chromium checks for named sources and mocked Lounge cards.

Run only in a verified separate PID namespace with private proc, like other browsers.
"""
from pathlib import Path
import tempfile
import threading

from PIL import ImageFont

from mkw_tracker.server import Application, make_server
from tests.test_overlay_profiles import payload


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / 'fonts').mkdir()
        source_font = root / 'fonts' / 'Source Preview.ttf'
        source_font.write_bytes(ImageFont.load_default(size=20).path.getvalue())
        app = Application(root / 'scores.sqlite', root)
        requests = []
        def fetch(name, *_):
            requests.append(name)
            return payload(name, winRate=.5, averageScore=92, maxMmr=7500,
                           winsLastTen=1, lossesLastTen=1, gainLossLastTen=25)
        app.lounge.fetch = fetch
        app.store.create_team('Local team')
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                page.locator('#tab-overlays').click()
                expect(page.locator('#overlay-source')).to_have_value('default')
                # Hold an old GET across mutations: refresh() deliberately returns
                # early when another request is in flight. The source editor must
                # use its own post-mutation revision, even after the old GET lands.
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                held = []
                def hold_state(route):
                    response = route.fetch()
                    held.append((route, response.body()))
                def begin_old_state():
                    page.route('**/api/state', hold_state)
                    page.evaluate('void refresh()')
                    for _ in range(40):
                        if held:
                            return
                        page.wait_for_timeout(25)
                    raise AssertionError('Old state request was not intercepted')
                def release_old_state():
                    route, body = held.pop()
                    route.fulfill(status=200, content_type='application/json', body=body)
                    page.unroute('**/api/state', hold_state)
                    page.wait_for_function('!refreshing')
                begin_old_state()
                page.locator('#overlay-source-create [name=name]').fill('Player cards')
                page.locator('#overlay-source-create [name=slug]').fill('cards')
                page.locator('#overlay-source-create button').click()
                expect(page.locator('#overlay-source')).to_have_value('cards')
                expect(page.locator('#overlay-url')).to_have_value(base + '/overlay/cards')
                release_old_state()
                expect(page.locator('#overlay-source')).to_have_value('cards')
                expect(page.locator('#overlay-url')).to_have_value(base + '/overlay/cards')
                page.locator('summary').filter(has_text='Source content and Lounge cards').click()
                page.locator('#source-header').uncheck()
                page.locator('#source-standings').uncheck()
                page.locator('#source-lounge').check()
                page.locator('#lounge-enabled').check()
                begin_old_state()
                page.locator('#lounge-usernames').fill('Example\nOther <name>')
                page.locator('#lounge-season').fill('3')
                expect(page.locator('#lounge-background')).to_have_value('aurora')
                expect(page.locator('#lounge-border')).to_have_value('rainbow')
                expect(page.locator('#lounge-shape')).to_have_value('pill')
                expect(page.locator('#lounge-metric-username')).to_be_checked()
                page.locator('#lounge-background').select_option('carbon')
                page.locator('#lounge-border').select_option('division')
                page.locator('#lounge-shape').select_option('rounded')
                font_select = page.locator('select#overlay-source-font')
                expect(font_select.locator('option', has_text='Source Preview')).to_have_count(1)
                expect(font_select).to_have_value('')
                font_select.select_option(source_font.name)

                page.locator('#lounge-metric-average_score').uncheck()
                page.locator('#overlay-source-settings button[type=submit]').click()
                expect(page.locator('#message')).to_contain_text('preferences saved')
                expect(page.locator('#lounge-usernames')).to_have_value('Example\nOther <name>')
                release_old_state()
                expect(page.locator('#lounge-usernames')).to_have_value('Example\nOther <name>')
                expect(page.locator('#lounge-background')).to_have_value('carbon')
                expect(page.locator('#lounge-border')).to_have_value('division')
                expect(page.locator('#lounge-shape')).to_have_value('rounded')
                page.locator('#lounge-metric-leaderboard_rank').uncheck()
                page.locator('#overlay-source-settings button[type=submit]').click()
                expect(page.locator('#overlay-source-settings button[type=submit]')).to_be_enabled()
                assert app.overlay_profiles.get('cards')['lounge']['players'] == ['Example', 'Other <name>']
                assert app.overlay_profiles.get('cards')['lounge']['appearance'] == {
                    'background': 'carbon', 'border': 'division', 'shape': 'rounded'}
                assert app.overlay_profiles.get('cards')['report_font'] == source_font.name
                assert app.settings['report_font'] == ''
                assert requests == [], requests  # saves do not trigger remote requests
                source = browser.new_page(viewport={'width': 900, 'height': 900})
                source.on('pageerror', lambda error: errors.append(str(error)))
                source.goto(base + '/overlay/cards')
                expect(source.locator('.lounge-card')).to_have_count(2)
                expect(source.locator('.lounge-name').last).to_have_text('Other <name>')
                expect(source.locator('.lounge-card').first).to_contain_text('50%', timeout=7000)
                expect(source.locator('.lounge-card').first).not_to_contain_text('Average event score · season')
                expect(source.locator('.standing-card')).to_have_count(0)
                expect(source.locator('#overlay-header')).to_be_hidden()
                assert source.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgba(0, 0, 0, 0)'
                legacy = browser.new_page()
                legacy.goto(base + '/overlay')
                expect(legacy.locator('.standing-card')).to_have_count(1)
                expect(legacy.locator('.lounge-card')).to_have_count(0)
                # Per-source settings and default URL retain independent behavior.
                page.locator('#overlay-settings [name=width]').fill('700')
                page.locator('#overlay-settings button').click()
                expect(source.locator('#overlay')).to_have_css('width', '700px', timeout=6000)
                expect(legacy.locator('#overlay')).to_have_css('width', '560px')
                page.locator('#overlay-hide').click()
                expect(source.locator('#overlay')).to_have_attribute('aria-hidden', 'true', timeout=6000)
                expect(legacy.locator('#overlay')).to_have_attribute('aria-hidden', 'false')
                # Unsaved drafts survive a polling update and save of the other form.
                page.locator('#lounge-usernames').fill('Unsaved draft')
                font_select.select_option('')
                page.locator('#lounge-background').select_option('transparent')
                page.locator('#lounge-border').select_option('none')
                page.locator('#lounge-shape').select_option('pill')
                page.locator('#lounge-metric-username').uncheck()

                page.locator('#overlay-settings [name=width]').fill('710')
                page.locator('#overlay-settings button').click()
                expect(source.locator('#overlay')).to_have_css('width', '710px', timeout=6000)
                expect(page.locator('#lounge-usernames')).to_have_value('Unsaved draft')
                expect(font_select).to_have_value('')
                # Refreshing the shared catalog must not discard a source font draft.
                page.evaluate('reloadFonts()')
                expect(font_select).to_have_value('')
                assert app.overlay_profiles.get('cards')['report_font'] == source_font.name
                assert app.settings['report_font'] == ''
                expect(page.locator('#lounge-background')).to_have_value('transparent')
                expect(page.locator('#lounge-border')).to_have_value('none')
                expect(page.locator('#lounge-shape')).to_have_value('pill')
                expect(page.locator('#lounge-metric-username')).not_to_be_checked()

                page.locator('#overlay-source-reset').click()
                expect(page.locator('#lounge-usernames')).to_have_value('Example\nOther <name>')
                expect(page.locator('#lounge-background')).to_have_value('carbon')
                expect(page.locator('#lounge-border')).to_have_value('division')
                expect(page.locator('#lounge-shape')).to_have_value('rounded')
                expect(page.locator('#lounge-metric-username')).to_be_checked()
                expect(font_select).to_have_value(source_font.name)
                # Reload/source selection restores the persisted appearance rather than defaults.
                page.reload()
                page.locator('#tab-overlays').click()
                page.locator('#overlay-source').select_option('cards')
                page.locator('summary').filter(has_text='Source content and Lounge cards').click()
                expect(page.locator('#lounge-background')).to_have_value('carbon')
                expect(page.locator('#lounge-border')).to_have_value('division')
                expect(page.locator('#lounge-shape')).to_have_value('rounded')
                expect(font_select).to_have_value(source_font.name)
                # Shared font catalog labels/options match, without free-text path entry.
                global_choices = page.locator('#report-font option').evaluate_all(
                    '(options) => options.filter(o => o.value).map(o => [o.value, o.textContent])')
                source_choices = font_select.locator('option').evaluate_all(
                    '(options) => options.filter(o => o.value).map(o => [o.value, o.textContent])')
                assert source_choices == global_choices
                # A missing saved font remains selected and honestly labeled after catalog reload.
                source_font.unlink()
                page.evaluate('reloadFonts()')
                expect(font_select).to_have_value(source_font.name)
                expect(font_select.locator('option:checked')).to_contain_text('Saved font unavailable')
                assert app.overlay_profiles.get('cards')['report_font'] == source_font.name
                font_select.select_option('')
                page.locator('#overlay-source-settings button[type=submit]').click()
                expect(page.locator('#message')).to_contain_text('preferences saved')
                assert app.overlay_profiles.get('cards')['report_font'] is None
                assert app.settings['report_font'] == ''
                # Moving content removes it from /overlay without destroying settings.
                page.locator('#overlay-move-block').select_option('standings')
                page.locator('#overlay-move-target').select_option('cards')
                page.locator('#overlay-move').click()
                expect(legacy.locator('.standing-card')).to_have_count(0, timeout=6000)
                expect(source.locator('.standing-card')).to_have_count(1, timeout=6000)
                # Failed source polls retain cards and explicit stale connection status.
                source.route('**/api/overlay/cards', lambda route: route.fulfill(status=503, body='unavailable'))
                expect(source.locator('#connection-status')).to_contain_text('last update', timeout=6000)
                expect(source.locator('.lounge-card')).to_have_count(2)
                source.unroute('**/api/overlay/cards')
                # WW suppresses cards and network even when configured/opted-in.
                count = len(requests)
                with app.lock:
                    app.switch_tracker_mode('ww', app.store.active_session()['id'])
                expect(source.locator('.lounge-card')).to_have_count(0, timeout=6000)
                assert len(requests) == count
                assert not errors, errors
                browser.close()
                print('PASS: source CRUD UI, independent URLs/settings/visibility, assignment, multiple manual Lounge cards, appearance/font catalog save/reload/drafts/inheritance, metric toggles, safe text, drafts, transparency, last-good polls and WW opt-out.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
