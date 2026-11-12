"""Local-only Lounge size recommendations and independent source drafts.

Run only in a verified separate PID namespace with private proc.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server
from tests.browser_lounge_cards import sample


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        requests = []
        app.lounge.fetch = lambda *args: requests.append(args)
        app.overlay_profiles.create('cards', 'Cards', app.settings)
        app.overlay_profiles.update('cards', {'content': {'header': False, 'standings': False, 'lounge': True},
                                             'lounge': {'players': ['Example']}})
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                page.locator('#tab-overlays').click()
                page.locator('#overlay-source').select_option('cards')
                page.locator('summary').filter(has_text='Source content and Lounge cards').click()
                recommendation = page.locator('#lounge-size-recommendation')
                use = page.locator('#lounge-use-size')
                width = page.locator('#overlay-settings [name=width]')
                height = page.locator('#overlay-settings [name=height]')
                font = page.locator('#overlay-settings [name=font_size]')

                def size():
                    return tuple(int(recommendation.get_attribute(f'data-{key}')) for key in ('width', 'height'))

                expect(recommendation).to_contain_text('estimated')
                expect(use).to_be_enabled()
                expect(page.locator('#lounge-metric-last_five_form')).not_to_be_checked()
                expanded = size()
                for checkbox in page.locator('#lounge-metrics input').all():
                    checkbox.uncheck()
                expect(use).to_be_disabled()
                # A persistently disabled helper is not an in-flight mutation:
                # it must not freeze ordinary background dashboard updates.
                page.evaluate('document.activeElement.blur()')
                assert page.evaluate('canRedraw()')
                for key in ('username', 'mmr', 'division'):
                    page.locator(f'#lounge-metric-{key}').check()
                compact = size()
                assert compact[0] < expanded[0], (compact, expanded)
                assert 240 <= compact[0] <= 1920 and 40 <= compact[1] < 120

                page.locator('#lounge-metric-last_five_form').check()
                with_form = size()
                assert with_form[0] > compact[0], (with_form, compact)
                page.locator('#lounge-usernames').fill('Example\nAnother player\nThird player')
                multiple = size()
                assert multiple[1] > with_form[1], (multiple, with_form)
                font.fill('36')
                larger_font = size()
                assert larger_font[0] > multiple[0] and larger_font[1] > multiple[1]
                # A live refresh cannot reset the current selections or estimate.
                page.evaluate('refresh()')
                expect(recommendation).to_have_attribute('data-width', str(larger_font[0]))
                expect(font).to_have_value('36')
                expect(width).to_have_value('560')
                expect(height).to_have_value('600')
                saved = app.overlay_profiles.get('cards')['overlay'].copy()
                use.click()
                expect(width).to_have_value(str(larger_font[0]))
                expect(height).to_have_value(str(larger_font[1]))
                assert app.overlay_profiles.get('cards')['overlay'] == saved
                assert app.overlay_profiles.get('cards')['lounge']['players'] == ['Example']
                assert requests == []
                page.locator('#overlay-settings button').click()
                expect(page.locator('#message')).to_contain_text('overlay settings saved')
                assert app.overlay_profiles.get('cards')['overlay']['width'] == larger_font[0]
                assert app.overlay_profiles.get('cards')['overlay']['height'] == larger_font[1]
                assert app.settings['overlay']['width'] == 560
                expect(page.locator('#lounge-usernames')).to_have_value('Example\nAnother player\nThird player')
                expect(page.locator('#lounge-metric-last_five_form')).to_be_checked()

                # Content saving is independent of applying/saving dimensions.
                page.locator('#overlay-source-settings button[type=submit]').click()
                expect(page.locator('#message')).to_contain_text('preferences saved')
                assert app.overlay_profiles.get('cards')['lounge']['metrics']['last_five_form']
                assert requests == []

                # A compact recommendation can be explicitly saved below the old 120px floor.
                page.locator('#lounge-usernames').fill('Example')
                for checkbox in page.locator('#lounge-metrics input').all():
                    checkbox.uncheck()
                for key in ('username', 'mmr', 'division'):
                    page.locator(f'#lounge-metric-{key}').check()
                font.fill('22')
                short = size()
                assert 40 <= short[1] < 120, short
                use.click()
                page.locator('#overlay-settings button').click()
                expect(page.locator('#message')).to_contain_text('overlay settings saved')
                assert app.overlay_profiles.get('cards')['overlay']['height'] == short[1]

                page.locator('#source-standings').check()
                expect(recommendation).to_contain_text('other visible content')
                expect(use).to_be_disabled()
                page.locator('#source-standings').uncheck()
                page.locator('#source-header').check()
                expect(use).to_be_disabled()
                page.locator('#overlay-settings [name=show_header]').uncheck()
                expect(use).to_be_enabled()
                page.locator('#source-header').uncheck()

                # No remote reads are needed even for intentionally oversized drafts.
                page.locator('#lounge-usernames').fill('\n'.join('名' * 63 + str(index) for index in range(8)))
                for checkbox in page.locator('#lounge-metrics input').all():
                    checkbox.check()
                font.fill('48')
                bounded = size()
                assert bounded[0] == 1920 and 40 <= bounded[1] <= 2160, bounded
                page.locator('#lounge-usernames').fill('')
                expect(use).to_be_disabled()
                assert recommendation.get_attribute('data-width') is None
                page.locator('#lounge-usernames').fill('Example')
                page.locator('#source-lounge').uncheck()
                expect(use).to_be_disabled()
                page.locator('#source-lounge').check()
                expect(use).to_be_enabled()
                # Compare the actual rendered overlay against the proposed dimensions.
                # Both thin hero cards and all-stat cards must fit without scrolling/cropping.
                preview = browser.new_page(viewport={'width': 1920, 'height': 2160})
                preview.on('pageerror', lambda error: errors.append(str(error)))
                rendered = sample()
                preview.route('**/api/overlay/cards', lambda route: route.fulfill(
                    status=200, content_type='application/json', body=json.dumps(rendered)))
                fitting_cases = oversized_cases = 0
                for font_size in (12, 22, 48):
                    for player_count in (1, 3, 8):
                        for all_stats in (False, True):
                            names = [f'Example{index}' for index in range(player_count)]
                            page.locator('#lounge-usernames').fill('\n'.join(names))
                            font.fill(str(font_size))
                            for checkbox in page.locator('#lounge-metrics input').all():
                                key = checkbox.get_attribute('id').removeprefix('lounge-metric-')
                                checkbox.set_checked(all_stats or key in ('username', 'mmr', 'division'))
                            proposed_width, proposed_height = size()
                            rendered = sample()
                            rendered['settings'].update(width=proposed_width, height=proposed_height, font_size=font_size)
                            rendered['lounge']['metrics'] = {
                                key: all_stats or key in ('username', 'mmr', 'division')
                                for key in rendered['lounge']['metrics']}
                            prototype = rendered['lounge']['players'][0]
                            rendered['lounge']['players'] = []
                            for name in names:
                                player = deepcopy(prototype)
                                player['username'] = player['data']['name'] = name
                                rendered['lounge']['players'].append(player)
                            preview.goto(f'http://127.0.0.1:{server.server_address[1]}/overlay/cards')
                            expect(preview.locator('.lounge-card')).to_have_count(player_count)
                            preview.evaluate('document.fonts.ready')
                            dimensions = preview.evaluate('''() => ({
                                content: document.querySelector('#standings').scrollHeight,
                                viewport: document.querySelector('#standings-viewport').clientHeight,
                                width: document.querySelector('#standings').scrollWidth,
                                available: document.querySelector('#standings-viewport').clientWidth
                            })''')
                            if 'height limit is reached' in recommendation.inner_text():
                                oversized_cases += 1
                                expect(use).to_be_disabled()
                                assert proposed_height == 2160
                            else:
                                fitting_cases += 1
                                assert dimensions['content'] <= dimensions['viewport'], (
                                    font_size, player_count, all_stats, size(), dimensions)
                            assert dimensions['width'] <= dimensions['available'], (
                                font_size, player_count, all_stats, size(), dimensions)
                assert (fitting_cases, oversized_cases) == (17, 1), (fitting_cases, oversized_cases)
                preview.close()
                # Mode changes also disable an already dirty Mogi draft.
                with app.lock:
                    app.switch_tracker_mode('ww', app.store.active_session()['id'])
                page.evaluate('refresh()')
                expect(use).to_be_disabled()
                expect(recommendation).to_contain_text('Mogi Teams or FFA')
                assert requests == []
                assert not errors, errors
                browser.close()
                print('PASS: local size estimates, draft metric/name/font updates, form toggle, source isolation, apply/save separation, bounds and mixed/WW guards.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
