"""Optional Chromium WW acceptance using temporary data, without live capture.

PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/browser-venv/bin/python -m tests.browser_ww
"""

import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server


def main():
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        app = Application(directory / 'scores.sqlite', directory)
        # Original synthetic icon retains independent icon/header-toggle coverage.
        from mkw_tracker.themes import resolve_theme
        palette = resolve_theme('mario')
        icon = BytesIO()
        Image.new('RGB', (32, 32), '#205080').save(icon, format='PNG')
        theme = app.theme_library.save({'name': 'Synthetic blue', 'tokens': palette['tokens'],
                                        'team_colors': palette['team_colors'],
                                        'icon_base64': base64.b64encode(icon.getvalue()).decode('ascii')})
        app.update_settings({'theme': theme['id']})
        player_ids = [app.store.add_player(f'Mogi player {index}')['id'] for index in range(12)]
        app.store.create_race('mogi-race', 'Rainbow Road')
        app.store.submit_result('mogi-race', player_ids)
        mogi_session = app.store.snapshot()['session']['id']
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                expect(page.locator('#mode')).to_have_text('Local race tracker')
                page.locator('#auto-refresh').uncheck()
                page.locator('#tracker-mode').select_option('ww')
                expect(page.locator('#ww-section')).to_be_visible()
                expect(page.locator('#tracker-mode option[value=ww]')).to_have_text('Worldwides (WW)')
                expect(page.locator('#ww-section h2')).to_have_text('Worldwides session')
                expect(page.locator('#mode')).to_have_text('Worldwides · automatic')
                expect(page.locator('#team-section')).to_be_hidden()
                expect(page.locator('#player-section')).to_be_hidden()
                expect(page.locator('#ww-metrics')).to_contain_text('Unknown')
                expect(page.locator('#ww-courses')).to_contain_text('No course statistics')
                ww_session = page.request.get(base + '/api/state').json()['session']['id']
                assert ww_session != mogi_session
                name = '選手☆ <img src=x onerror=alert(1)>'
                page.locator('#ww-player input').fill(name)
                page.locator('#ww-player button').click()
                expect(page.locator('#message')).to_contain_text('WW player label saved')
                assert page.locator('#ww-section img').count() == 0
                page.locator('#tab-capture').click()
                expect(page.locator('#camera-settings select[name=name_engine]')).to_be_disabled()
                expect(page.locator('#review-help')).to_contain_text('automatically')
                page.locator('#tab-videos').click()
                expect(page.locator('#import-video select[name=name_engine]')).to_be_disabled()

                def add_result(position, final, before='', change=''):
                    page.locator('#tab-history').click()
                    page.locator('#add-race input[name=course]').fill('Koopa Troopa Beach')
                    page.locator('#add-race button').click()
                    expect(page.locator('#ww-editor-panel')).to_be_visible()
                    expect(page.locator('#ww-results input[name=lobby_size]')).to_have_count(0)
                    for key, value in [('position', position), ('vr_after', final),
                                       ('vr_before', before), ('delta', change)]:
                        page.locator(f'#ww-results input[name={key}]').fill(str(value))
                    page.locator('#ww-results button[type=submit], #ww-results button:not([type])').click()
                    expect(page.locator('#ww-editor-panel')).to_be_hidden()

                add_result(18, 8759, 8805, -46)
                # Removing the lobby editor must not erase existing saved data.
                with app.lock:
                    legacy_race = app.store.snapshot()['races'][0]
                    legacy_race = app.store.set_ww_result(legacy_race['id'], position=18, vr_before=8805,
                                                          vr_after=8759, lobby_size=23)
                page.locator('#refresh').click()
                expect(page.locator('#history')).to_contain_text(f"revision {legacy_race['revision']}")
                expect(page.locator('#history tbody tr td').nth(1)).to_have_text('18')
                expect(page.locator('#history')).not_to_contain_text('18 / 23')
                page.locator('#history').get_by_role('button', name='Correct WW result').click()
                page.locator('#ww-results button:not([type])').click()
                expect(page.locator('#ww-editor-panel')).to_be_hidden()
                saved = page.request.get(base + '/api/state').json()['races'][0]
                assert saved['ww_result']['lobby_size'] == 23
                # A corrected place can invalidate hidden legacy metadata.
                page.locator('#history').get_by_role('button', name='Correct WW result').click()
                page.locator('#ww-results input[name=position]').fill('24')
                page.locator('#ww-results button:not([type])').click()
                expect(page.locator('#ww-editor-panel')).to_be_hidden()
                saved = page.request.get(base + '/api/state').json()['races'][0]
                assert saved['ww_result']['position'] == 24
                assert saved['ww_result']['lobby_size'] is None
                add_result(16, 8736)
                expect(page.locator('#history tbody tr').first.locator('td').nth(1)).to_have_text('16')
                expect(page.locator('#history')).not_to_contain_text('16 / ?')
                expect(page.locator('#history')).not_to_contain_text('Lounge')
                expect(page.locator('#history').get_by_role('button', name='View folder PNG')).to_have_count(1)
                page.locator('#tab-standings').click()
                expect(page.locator('#ww-metrics')).to_contain_text('8805')
                expect(page.locator('#ww-metrics')).to_contain_text('8736')
                expect(page.locator('#ww-summary')).to_contain_text('1 known changes')
                expect(page.locator('#ww-summary')).to_contain_text('Session change: Unknown')
                expect(page.locator('#ww-courses tbody tr')).to_have_count(1)
                expect(page.locator('#ww-courses tbody tr')).to_contain_text('-46')
                # The cut result is a separate observed point, not an invented change.
                path = page.locator('#ww-graph path').get_attribute('d')
                assert path.count('M') == 2, path

                overlay = browser.new_page(viewport={'width': 800, 'height': 700})
                overlay.on('pageerror', lambda error: errors.append(str(error)))
                overlay.goto(base + '/overlay')
                expect(overlay.locator('.ww-overlay-player')).to_have_text(name)
                expect(overlay.locator('.ww-overlay-track')).to_have_count(0)
                expect(overlay.locator('.ww-last_10_change .ww-overlay-label')).to_have_text('Last 2 races VR change')
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('Unknown')
                with app.lock:
                    app.capture.state.update(session_id=ww_session, course='Rainbow Road')
                expect(overlay.locator('.ww-overlay-track')).to_have_text('Current track: Rainbow Road', timeout=5000)
                with app.lock:
                    app.capture.state['course'] = 'Crown City'
                expect(overlay.locator('.ww-overlay-track')).to_have_text('Current track: Crown City', timeout=5000)
                # Track names remain literal text even for a malformed payload.
                payload = page.request.get(base + '/api/overlay').json()
                unsafe_track = '<img src=x onerror=alert(1)>'
                payload['ww']['current_track'] = unsafe_track
                payload['settings']['show_header'] = True
                overlay.route('**/api/overlay', lambda route: route.fulfill(
                    content_type='application/json', body=json.dumps(payload)))
                expect(overlay.locator('.ww-overlay-track')).to_have_text(f'Current track: {unsafe_track}', timeout=5000)
                expect(overlay.locator('.overlay-room')).to_contain_text('Worldwides')
                expect(overlay.locator('.ww-overlay-player-row img')).to_have_count(0)
                overlay.unroute('**/api/overlay')
                expect(overlay.locator('.ww-overlay-track')).to_have_text('Current track: Crown City', timeout=5000)
                with app.lock:
                    app.capture.state['course'] = None
                expect(overlay.locator('.ww-overlay-track')).to_have_count(0, timeout=5000)
                with app.lock:
                    app.capture.state['course'] = 'Great ? Block Ruins'
                expect(overlay.locator('.ww-overlay-track')).to_have_text('Current track: Great ? Block Ruins', timeout=5000)
                player_box = overlay.locator('.ww-overlay-player').bounding_box()
                track_box = overlay.locator('.ww-overlay-track').bounding_box()
                assert track_box['x'] >= player_box['x'] + player_box['width'], (player_box, track_box)
                overlay.set_viewport_size({'width': 240, 'height': 700})
                assert overlay.locator('.ww-overlay-player-row').evaluate('(node) => node.scrollWidth <= node.clientWidth')
                assert overlay.locator('.ww-overlay-track').evaluate('(node) => node.scrollWidth <= node.clientWidth')
                narrow_payload = page.request.get(base + '/api/overlay').json()
                narrow_payload['settings'].update(width=240, font_size=48)
                narrow_payload['ww']['last_10_race_count'] = 10
                overlay.route('**/api/overlay', lambda route: route.fulfill(
                    content_type='application/json', body=json.dumps(narrow_payload)))
                expect(overlay.locator('.ww-last_10_change .ww-overlay-label')).to_have_text('Last 10 races VR change', timeout=5000)
                for change, text in [(None, 'Unknown'), (999980, '+999980'), (-999980, '-999980')]:
                    narrow_payload['ww']['last_10_change'] = change
                    expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text(text, timeout=5000)
                    overlay.evaluate('document.fonts.ready')
                    dimensions = overlay.locator('.ww-last_10_change').evaluate('''node => {
                        const card = node.getBoundingClientRect();
                        const value = node.querySelector('.ww-overlay-value').getBoundingClientRect();
                        return {scrollWidth: node.scrollWidth, clientWidth: node.clientWidth,
                            cardLeft: card.left, cardRight: card.right, valueLeft: value.left, valueRight: value.right};
                    }''')
                    assert dimensions['scrollWidth'] <= dimensions['clientWidth'], dimensions
                    assert dimensions['cardLeft'] <= dimensions['valueLeft'], dimensions
                    assert dimensions['valueRight'] <= dimensions['cardRight'], dimensions
                overlay.unroute('**/api/overlay')
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('Unknown', timeout=5000)
                overlay.set_viewport_size({'width': 800, 'height': 700})
                expect(overlay.locator('.ww-current_vr')).to_contain_text('8736')
                expect(overlay.locator('.ww-peak_vr')).to_contain_text('8805')
                expect(overlay.locator('.ww-lowest_vr')).to_contain_text('8736')
                expect(overlay.locator('.ww-overlay-graph svg')).to_have_count(1)
                assert 'Racers Delight' not in overlay.locator('.ww-overlay-player').evaluate('(node) => getComputedStyle(node).fontFamily')
                assert 'Racers Delight' not in overlay.locator('.ww-overlay-value').first.evaluate('(node) => getComputedStyle(node).fontFamily')
                evidence = Path('.omx/evidence')
                evidence.mkdir(parents=True, exist_ok=True)
                image = Image.open(BytesIO(overlay.screenshot(path=str(evidence / 'ww-overlay.png'), omit_background=True))).convert('RGBA')
                assert image.getpixel((0, 0))[3] == 0
                assert image.getpixel((790, 690))[3] == 0
                overlay.locator('.ww-current_vr').evaluate('(node) => node.dataset.kept = "yes"')
                overlay.wait_for_timeout(1700)
                expect(overlay.locator('.ww-current_vr')).to_have_attribute('data-kept', 'yes')

                page.locator('#tab-overlays').click()
                expect(page.locator('#overlay-settings select[name=layout]')).to_be_hidden()
                page.locator('#overlay-settings input[name=ww_show_graph]').uncheck()
                page.locator('#overlay-settings input[name=ww_show_lowest]').uncheck()
                page.locator('#overlay-settings button').click()
                expect(page.locator('#message')).to_contain_text('overlay settings saved')
                expect(overlay.locator('.ww-overlay-graph')).to_have_count(0, timeout=5000)
                expect(overlay.locator('.ww-lowest_vr')).to_have_count(0)
                expect(overlay.locator('.ww-current_vr')).to_have_count(1)
                expect(overlay.locator('.ww-peak_vr')).to_have_count(1)
                # Header text and character remain independently selectable.
                header_control = page.locator('#overlay-settings input[name=show_header]')
                character_control = page.locator('#overlay-settings input[name=ww_show_character]')
                recent_control = page.locator('#overlay-settings input[name=ww_show_last_10]')
                expect(character_control).to_be_checked()
                expect(recent_control).to_be_checked()
                header_control.uncheck()
                page.locator('#overlay-settings button').click()
                expect(overlay.locator('.overlay-title')).to_have_count(0, timeout=5000)
                expect(overlay.locator('.overlay-room')).to_have_count(0)
                expect(overlay.locator('.overlay-character')).to_be_visible()
                character_control.uncheck()
                recent_control.uncheck()
                page.locator('#overlay-settings button').click()
                expect(overlay.locator('#overlay-header')).to_be_hidden(timeout=5000)
                expect(overlay.locator('.ww-last_10_change')).to_have_count(0)
                page.reload()
                page.locator('#auto-refresh').uncheck()
                page.locator('#tab-overlays').click()
                expect(header_control).not_to_be_checked()
                expect(character_control).not_to_be_checked()
                expect(recent_control).not_to_be_checked()
                header_control.check()
                page.locator('#overlay-settings button').click()
                expect(overlay.locator('.overlay-title')).to_be_visible(timeout=5000)
                expect(overlay.locator('.overlay-room')).to_be_visible()
                expect(overlay.locator('.overlay-character')).to_have_count(0)
                character_control.check()
                recent_control.check()
                page.locator('#overlay-settings button').click()
                expect(overlay.locator('.overlay-character')).to_be_visible(timeout=5000)
                expect(overlay.locator('.overlay-title')).to_be_visible()
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('Unknown')
                overlay.emulate_media(reduced_motion='reduce')
                expect(overlay.locator('#overlay')).to_have_css('transition-duration', '0s')
                # Legacy hotkeys send no mode tag, with either no body or {}.
                response = page.request.post(base + '/api/overlay/toggle')
                assert response.ok and response.json() == {'visible': False}
                expect(overlay.locator('#overlay')).to_have_attribute('aria-hidden', 'true')
                expect(overlay.locator('#overlay')).to_have_css('visibility', 'hidden')
                response = page.request.post(base + '/api/overlay/toggle', data={})
                assert response.ok and response.json() == {'visible': True}
                expect(overlay.locator('#overlay')).to_have_attribute('aria-hidden', 'false')
                expect(overlay.locator('#overlay')).to_have_css('visibility', 'visible')
                overlay.route('**/api/overlay', lambda route: route.abort())
                expect(overlay.locator('#connection-status')).to_contain_text('showing last update', timeout=6000)
                expect(overlay.locator('.ww-current_vr')).to_contain_text('8736')
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('Unknown')
                expect(overlay.locator('.ww-overlay-track')).to_have_text('Current track: Great ? Block Ruins')
                overlay.unroute('**/api/overlay')

                # Corrections replace the old race contribution.
                page.locator('#tab-history').click()
                page.locator('#history').get_by_role('button', name='Correct WW result').first.click()
                page.locator('#ww-results input[name=vr_before]').fill('8759')
                page.locator('#ww-results input[name=delta]').fill('-23')
                page.locator('#ww-results button:not([type])').click()
                expect(page.locator('#ww-editor-panel')).to_be_hidden()
                page.locator('#tab-standings').click()
                expect(page.locator('#ww-summary')).to_contain_text('2 known changes')
                expect(page.locator('#ww-summary')).to_contain_text('Session change: -69')
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('-69', timeout=5000)
                # Generic chapter editing/export remains available in WW.
                page.locator('#tab-chapters').click()
                page.locator('#add-recording input').fill('WW edited recording')
                page.locator('#add-recording button').click()
                expect(page.locator('#export-recording option')).to_have_count(2)
                recording = page.request.get(base + '/api/state').json()['recordings'][0]
                page.locator('#tab-history').click()
                page.locator('#history').get_by_role('button', name='Edit course / chapter').first.click()
                page.locator('#timing-recording').select_option(recording['id'])
                page.locator('#race-timing input[name=offset]').fill('12.345')
                page.locator('#race-timing button:not([type])').click()
                expect(page.locator('#timing-panel')).to_be_hidden()
                page.locator('#tab-chapters').click()
                page.locator('#export-recording').select_option(recording['id'])
                with page.expect_download() as download_info:
                    page.locator('#export button').click()
                assert Path(download_info.value.path()).read_text(encoding='utf-8') == '00:00:12.345 Koopa Troopa Beach\n'
                page.locator('#tab-history').click()
                page.locator('#history').get_by_role('button', name='View folder PNG').click()
                expect(page.locator('#folder-png-preview')).to_be_visible()
                expect(page.locator('#folder-png-team')).to_be_hidden()
                expect(page.locator('#folder-png-image')).to_be_visible(timeout=15000)
                expect(page.locator('#folder-png-copy')).to_be_enabled()
                page.locator('#folder-png-close').click()
                expect(page.locator('#history').get_by_role('button', name='View folder PNG')).to_be_focused()
                page.locator('#tab-standings').click()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(evidence / 'ww-dashboard-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})

                page.locator('#tracker-mode').select_option('mogi')
                expect(page.locator('#team-section')).to_be_visible()
                expect(page.locator('#ww-section')).to_be_hidden()
                expect(page.locator('#room-points')).to_contain_text('82 / 984')
                restored = page.request.get(base + '/api/state').json()
                assert restored['session']['id'] == mogi_session
                assert len(restored['races']) == 1
                expect(overlay.locator('.ww-last_10_change')).to_have_count(0, timeout=5000)
                # The WW-only character preference must not change Mogi headers.
                with app.lock:
                    app.update_settings({'overlay': {'ww_show_character': False}})
                expect(overlay.locator('.overlay-title')).to_be_visible()
                expect(overlay.locator('.overlay-character')).to_be_visible()
                page.locator('#tracker-mode').select_option('ww')
                expect(overlay.locator('.overlay-character')).to_have_count(0, timeout=5000)
                expect(overlay.locator('.overlay-title')).to_be_visible()
                expect(page.locator('#ww-summary')).to_contain_text('Session change: -69')
                assert page.request.get(base + '/api/state').json()['session']['id'] == ww_session
                page.locator('#tab-capture').click()
                page.locator('#new-session input').fill('Second WW session')
                page.locator('#new-session button').click()
                page.locator('#tab-standings').click()
                expect(page.locator('#ww-metrics')).to_contain_text('Unknown')
                expect(page.locator('#ww-courses')).to_contain_text('No course statistics')
                page.locator('#ww-stats-scope').select_option('all')
                expect(page.locator('#ww-courses tbody tr')).to_have_count(1)
                expect(overlay.locator('.ww-last_10_change .ww-overlay-label')).to_have_text('Last 10 races VR change', timeout=5000)
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('Unknown')
                # Prefer finishing position even when VR changes rank courses oppositely.
                with app.lock:
                    for race_id, course, position, delta in [
                        ('stats-best', 'Boo Cinema', 2, -100),
                        ('stats-worse', 'Crown City', 8, 100),
                    ]:
                        app.store.create_race(race_id, course)
                        app.store.set_ww_result(race_id, position=position, vr_after=9000, delta=delta)
                page.locator('#refresh').click()
                page.locator('#ww-stats-scope').select_option('session')
                expect(page.locator('#ww-courses tbody tr td:first-child')).to_have_text(['Boo Cinema', 'Crown City'])
                expect(page.locator('#ww-section')).to_contain_text('Ranked by mean finishing position')
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('0', timeout=5000)
                with app.lock:
                    app.store.set_ww_result('stats-worse', position=8, vr_after=9000, delta=120)
                expect(overlay.locator('.ww-last_10_change .ww-overlay-value')).to_have_text('+20', timeout=5000)
                page.locator('#ww-stats-scope').select_option('all')
                expect(page.locator('#ww-courses tbody tr td:first-child')).to_have_text(
                    ['Boo Cinema', 'Crown City', 'Koopa Troopa Beach'])
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()
    print('WW browser acceptance passed: mode isolation, plain positions, legacy lobby preservation/correction, '
          'Worldwides labels, known-only manual stats, detected-track updates/text safety/narrow layout/last-good state, '
          'independent persistent header/character/recent-change toggles, rolling-change corrections, '
          'position-ranked current/all course statistics, bodyless/JSON hotkey toggles and mobile layout.')


if __name__ == '__main__':
    main()
